"""Handle joins, connections and commands. Serialise room changes and sends here."""

import asyncio
import secrets

from contextlib  import asynccontextmanager
from dataclasses import dataclass, field
from time        import monotonic

from fastapi  import WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .game import Action, Color, Game, InvalidAction
from .chat import Chat, ChatRoom 

SEND_TIMEOUT_SECONDS = 3


class Command(BaseModel):
    model_config = ConfigDict(extra = "forbid", strict = True)

    isChat   : bool = Field(default = False)

    # chess 
    type     : Action
    revision : int  = Field(ge = 0)
    move     : str  = Field(default = "", max_length = 5)

    # chat 
    content : str | None = Field(default = None)
    amount  : int        = Field(default = 0, ge = 0)


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

    _game     : Game                = field(default_factory = Game,         init = False)
    _players  : dict[Color, Player] = field(
        default_factory = lambda: {"white": Player()},
        init            = False,
    )
    _lock     : asyncio.Lock        = field(default_factory = asyncio.Lock, init = False)
    _chatRoom : ChatRoom            = field(default_factory = ChatRoom,     init = False)

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

            await self._broadcast()
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

                await self._broadcast()

            yield
        finally:
            async with self._lock:
                # Cleaning up the previous connection must not remove its replacement.
                if player.socket is socket:
                    player.socket      = None
                    self.last_activity = monotonic()
                    await self._broadcast()

    async def handle(self, color: Color, socket: WebSocket, message: str) -> bool:
        async with self._lock:
            player = self._players[color]
            if player.socket is not socket:
                return False

            # Validate the input format and game conditions, then apply the command once.
            error = None
            try:
                command = Command.model_validate_json(message)
            except ValidationError:
                error = "Invalid request format."
            else:
                try:
                    if command.isChat:
                        # Missing content requests history without adding a message.
                        if command.content is not None:
                            if not command.content:
                                raise InvalidAction("Chat messages must not be empty.")
                            if len(command.content) > 140:
                                raise InvalidAction("Chat messages must be at most 140 characters.")

                            self._chatRoom.append(Chat(sender = color, content = command.content))
                    else: 
                        if command.revision != self._game.revision:
                            raise InvalidAction("The game has changed. Please try again.")
                        if len(self._players) < 2:
                            raise InvalidAction("Please wait for your opponent to join.")
                        if command.type != "resign" and len(self.connected) < 2:
                            raise InvalidAction("Please wait for your opponent to reconnect.")

                        self._game.apply(color, command.type, command.move)
                except InvalidAction as rejection:
                    error = str(rejection)

            # Validation can fail before command is assigned; handle errors first.
            if error is not None:
                await player.send({"type": "error", "message": error})
            elif command.isChat:
                await player.send({
                    "type": "chat_history",
                    "logs": self._chatRoom.recent_logs(command.amount),
                })

            # Send the latest state even after a rejection to keep both views synchronised.
            self.last_activity = monotonic()
            await self._broadcast()
            return player.socket is socket

    async def _broadcast(self) -> None:
        # Only call this method from within the room while holding _lock.
        while self.connected:
            state = {
                **self._game.snapshot(),
                "joined"    : list(self._players),
                "connected" : self.connected,
            }

            for color, player in self._players.items():
                await player.send({
                    "type": "state", 
                    "you": color, 
                    "game": state,
                    "logs": self._chatRoom.recent_logs(5),
                })

            if state["connected"] == self.connected:
                break
            # Broadcast the updated connection status if a player disconnected during a send.
