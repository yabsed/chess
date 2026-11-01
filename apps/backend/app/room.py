"""Handle joins, connections and commands. Serialise room changes and sends here."""

import asyncio

from dataclasses import dataclass
from time        import monotonic

from pydantic import ValidationError
from socketio import AsyncServer

from .game     import Color, Game, InvalidAction
from .protocol import Command, Seat, User


class RoomFull(Exception):
    pass


@dataclass
class Player:
    user : User
    sid  : str | None = None


class Room:
    def __init__(self, room_id: str, socket: AsyncServer, white: User, black: User | None = None) -> None:
        self.id     = room_id
        self.socket = socket

        self._game = Game()
        self._lock = asyncio.Lock()

        self._players: dict[Color, Player] = {"white": Player(white)}
        if black is not None:
            self._players["black"] = Player(black)
        self.last_activity = monotonic()

    @property
    def finished(self) -> bool:
        return self._game.outcome is not None

    @property
    def has_opponent(self) -> bool:
        return "black" in self._players

    def color_for(self, user_id: int) -> Color | None:
        for color, player in self._players.items():
            if player.user.id == user_id:
                return color
        return None

    @property
    def connected(self) -> list[Color]:
        return [color for color, player in self._players.items() if player.sid is not None]

    async def join(self, user: User) -> None:
        async with self._lock:
            if self.color_for(user.id) is not None:
                return
            if "black" in self._players:
                raise RoomFull("This game already has two players.")

            self._players["black"] = Player(user)
            self.last_activity     = monotonic()

    async def connect(self, user: User, sid: str) -> None:
        async with self._lock:
            color = self.color_for(user.id)
            if color is None:
                raise ValueError("User does not belong to this room.")

            player   = self._players[color]
            previous = player.sid
            if previous is not None:
                await self.socket.leave_room(previous, self.id)

            player.user        = user
            player.sid         = sid
            self.last_activity = monotonic()

            seat = Seat(id = self.id, color = color)
            await self.socket.emit("seat", seat.model_dump(), to = sid)
            await self.socket.enter_room(sid, self.id)
            await self._broadcast()

    async def update_user(self, user: User) -> None:
        async with self._lock:
            color = self.color_for(user.id)
            if color is not None:
                self._players[color].user = user
                await self._broadcast()

    async def disconnect(self, user_id: int, sid: str) -> None:
        async with self._lock:
            color = self.color_for(user_id)
            if color is None:
                return

            player = self._players[color]
            if player.sid != sid:
                return

            player.sid         = None
            self.last_activity = monotonic()

            await self.socket.leave_room(sid, self.id)
            await self._broadcast()

    async def handle(self, user_id: int, sid: str, message: object) -> dict[str, str | None]:
        async with self._lock:
            color = self.color_for(user_id)
            if color is None or self._players[color].sid != sid:
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
            "players"   : {color: player.user.model_dump() for color, player in self._players.items()},
            "connected" : self.connected,
        }
        await self.socket.emit("state", state, to = self.id)
