"""Handle joins, connections and commands. Serialise room changes and sends here."""

import asyncio
import secrets

from contextlib  import asynccontextmanager
from dataclasses import asdict, dataclass, field
from time        import monotonic
from typing      import Annotated, Literal

from fastapi  import WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from .chat import Chat, ChatLog
from .game import Action, Color, Game, InvalidAction

SEND_TIMEOUT_SECONDS = 3
CHAT_HISTORY_LIMIT   = 20


class GameCommand(BaseModel):
    model_config = ConfigDict(extra = "forbid", strict = True)

    type     : Action
    revision : int  = Field(ge = 0)
    move     : str  = Field(default = "", max_length = 5)


class ChatCommand(BaseModel):
    model_config = ConfigDict(extra = "forbid", strict = True)

    type    : Literal["chat"]
    content : str = Field(min_length = 1, max_length = 140)


class ChatHistoryCommand(BaseModel):
    model_config = ConfigDict(extra = "forbid", strict = True)

    type  : Literal["chat_history"]
    limit : int = Field(default = CHAT_HISTORY_LIMIT, ge = 0)


command_parser = TypeAdapter(Annotated[
    GameCommand | ChatCommand | ChatHistoryCommand,
    Field(discriminator = "type"),
])


class RoomFull(Exception):
    pass


@dataclass
class Player:
    token  : str              = field(default_factory = lambda: secrets.token_urlsafe(32))
    socket : WebSocket | None = None

    async def send(self, message: dict) -> None:
        if self.socket is None:
            return

        try:
            await asyncio.wait_for(self.socket.send_json(message), timeout = SEND_TIMEOUT_SECONDS)
        except (WebSocketDisconnect, RuntimeError, OSError, TimeoutError):
            self.socket = None


@dataclass
class Room:
    last_activity: float = field(default_factory = monotonic)

    _game : Game    = field(default_factory = Game,    init = False)
    _chat : ChatLog = field(default_factory = ChatLog, init = False)

    _players: dict[Color, Player] = field(
        default_factory = lambda: {"white": Player()},
        init            = False,
    )
    _lock: asyncio.Lock = field(default_factory = asyncio.Lock, init = False)

    @property
    def white_token(self) -> str:
        return self._players["white"].token

    @property
    def connected(self) -> list[Color]:
        return [color for color, player in self._players.items() if player.socket is not None]

    async def join(self) -> str:
        async with self._lock:
            if "black" in self._players:
                raise RoomFull("This game already has two players.")

            player = Player()
            self._players["black"] = player
            self.last_activity = monotonic()

            await self._broadcast_game()
            return player.token

    def authenticate(self, token: str) -> Color | None:
        received_token = token.encode()

        for color, player in self._players.items():
            expected_token = player.token.encode()
            if secrets.compare_digest(expected_token, received_token):
                return color

        return None

    @asynccontextmanager
    async def connect(self, color: Color, socket: WebSocket):
        player = self._players[color]

        try:
            async with self._lock:
                previous = player.socket
                player.socket      = socket
                self.last_activity = monotonic()

                if previous is not None:
                    try:
                        await previous.close(4409, "Your seat is connected in another tab.")
                    except (WebSocketDisconnect, RuntimeError, OSError):
                        pass  # Keep the new connection even if the previous one has already closed.

                await player.send({
                    "type": "chat_history",
                    "logs": self._chat.recent(CHAT_HISTORY_LIMIT),
                })
                await self._broadcast_game()

            yield
        finally:
            async with self._lock:
                # Cleaning up the previous connection must not remove its replacement.
                if player.socket is socket:
                    player.socket      = None
                    self.last_activity = monotonic()
                    await self._broadcast_game()

    async def handle(self, color: Color, socket: WebSocket, message: str) -> bool:
        async with self._lock:
            player = self._players[color]
            if player.socket is not socket:
                return False

            self.last_activity = monotonic()

            try:
                command = command_parser.validate_json(message)
            except ValidationError:
                await player.send({"type": "error", "message": "Invalid request format."})
                await self._broadcast_game()
                return player.socket is socket

            if isinstance(command, GameCommand):
                await self._handle_game(color, command)
            elif isinstance(command, ChatCommand):
                await self._handle_chat(color, command)
            elif isinstance(command, ChatHistoryCommand):
                await self._handle_chat_history(color, command)

            return player.socket is socket

    # The handlers and broadcasts below run with the room's lock held.
    async def _handle_game(self, color: Color, command: GameCommand) -> None:
        try:
            if command.revision != self._game.revision:
                raise InvalidAction("The game has changed. Please try again.")
            if len(self._players) < 2:
                raise InvalidAction("Please wait for your opponent to join.")
            if command.type != "resign" and len(self.connected) < 2:
                raise InvalidAction("Please wait for your opponent to reconnect.")

            self._game.apply(color, command.type, command.move)
        except InvalidAction as rejection:
            await self._players[color].send({"type": "error", "message": str(rejection)})

        # Send the latest state even after a rejection to keep both views synchronised.
        await self._broadcast_game()

    async def _handle_chat(self, color: Color, command: ChatCommand) -> None:
        chat = Chat(sender = color, content = command.content)
        self._chat.append(chat)

        connected = self.connected
        for player in self._players.values():
            await player.send({"type": "chat", **asdict(chat)})

        if connected != self.connected:
            await self._broadcast_game()

    async def _handle_chat_history(self, color: Color, command: ChatHistoryCommand) -> None:
        player = self._players[color]
        await player.send({
            "type": "chat_history",
            "logs": self._chat.recent(command.limit),
        })

        if player.socket is None:
            await self._broadcast_game()

    async def _broadcast_game(self) -> None:
        while self.connected:
            state = {
                **self._game.snapshot(),
                "joined"    : list(self._players),
                "connected" : self.connected,
            }

            for color, player in self._players.items():
                await player.send({"type": "state", "you": color, "game": state})

            if state["connected"] == self.connected:
                break
            # Broadcast the updated connection status if a player disconnected during a send.
