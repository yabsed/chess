"""Handle joins, connections and commands. Serialise room changes and sends here."""

import asyncio
import secrets

from dataclasses import dataclass, field
from time        import monotonic

from pydantic import ValidationError
from socketio import AsyncServer

from .game     import Color, Game, InvalidAction
from .protocol import Command


class RoomFull(Exception):
    pass


@dataclass
class Player:
    token : str        = field(default_factory = lambda: secrets.token_urlsafe(32))
    sid   : str | None = None


class Room:
    def __init__(self, room_id: str, socket: AsyncServer) -> None:
        self.id     = room_id
        self.socket = socket

        self._game = Game()
        self._lock = asyncio.Lock()

        self._players: dict[Color, Player] = {"white": Player()}
        self.last_activity = monotonic()

    @property
    def white_token(self) -> str:
        return self._players["white"].token

    @property
    def connected(self) -> list[Color]:
        return [color for color, player in self._players.items() if player.sid is not None]

    async def join(self) -> str:
        async with self._lock:
            if "black" in self._players:
                raise RoomFull("This game already has two players.")

            player = Player()

            self._players["black"] = player
            self.last_activity     = monotonic()

            await self._broadcast()
            return player.token

    def authenticate(self, token: str) -> Color | None:
        received_token = token.encode()

        for color, player in self._players.items():
            expected_token = player.token.encode()
            if secrets.compare_digest(expected_token, received_token):
                return color

        return None

    async def connect(self, color: Color, sid: str) -> None:
        async with self._lock:
            player   = self._players[color]
            previous = player.sid
            if previous is not None:
                await self.socket.leave_room(previous, self.id)

            player.sid         = sid
            self.last_activity = monotonic()

            await self.socket.enter_room(sid, self.id)
            await self._broadcast()

        # Disconnect invokes our cleanup handler, which acquires the same lock.
        if previous is not None:
            await self.socket.emit("session_closed", "Your seat is connected in another tab.", to = previous)
            await self.socket.disconnect(previous)

    async def disconnect(self, color: Color, sid: str) -> None:
        async with self._lock:
            player = self._players[color]
            if player.sid != sid:
                return

            player.sid         = None
            self.last_activity = monotonic()

            await self.socket.leave_room(sid, self.id)
            await self._broadcast()

    async def handle(self, color: Color, sid: str, message: object) -> dict[str, str | None]:
        async with self._lock:
            if self._players[color].sid != sid:
                return {"error": "Your seat is connected in another tab."}

            error = None
            try:
                command = Command.model_validate(message)

                if command.revision != self._game.revision:
                    raise InvalidAction("The game has changed. Please try again.")
                if len(self._players) < 2:
                    raise InvalidAction("Please wait for your opponent to join.")
                if command.type != "resign" and len(self.connected) < 2:
                    raise InvalidAction("Please wait for your opponent to reconnect.")

                self._game.apply(color, command.type, command.move)
            except ValidationError:
                error = "Invalid request format."
            except InvalidAction as rejection:
                error = str(rejection)

            # Publish the latest state before replying, including after a rejection.
            self.last_activity = monotonic()
            await self._broadcast()
            return {"error": error}

    async def _broadcast(self) -> None:
        # Only call this method from within the room while holding _lock.
        state = {
            **self._game.snapshot(),
            "joined"    : list(self._players),
            "connected" : self.connected,
        }
        await self.socket.emit("state", state, to = self.id)
