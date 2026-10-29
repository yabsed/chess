"""참가, 연결, 명령을 처리한다. 방의 변경과 전송은 이 안에서 직렬화한다."""

import asyncio
import secrets

from contextlib  import asynccontextmanager
from dataclasses import dataclass, field
from time        import monotonic

from fastapi  import WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .game import Action, Color, Game, InvalidAction

SEND_TIMEOUT_SECONDS = 3


class Command(BaseModel):
    model_config = ConfigDict(extra = "forbid", strict = True)

    type     : Action
    revision : int = Field(ge = 0)
    move     : str = Field(default = "", max_length = 5)


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

    _game    : Game                = field(default_factory = Game,         init = False)
    _players : dict[Color, Player] = field(
        default_factory = lambda: {"white": Player()},
        init            = False,
    )
    _lock    : asyncio.Lock        = field(default_factory = asyncio.Lock, init = False)

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
                        pass  # 이미 끊긴 연결이어도 새 연결은 등록한다.

                await self._broadcast()

            yield
        finally:
            async with self._lock:
                # 이전 연결의 정리가 새 연결을 지우면 안 된다.
                if player.socket is socket:
                    player.socket      = None
                    self.last_activity = monotonic()
                    await self._broadcast()

    async def handle(self, color: Color, socket: WebSocket, message: str) -> bool:
        async with self._lock:
            player = self._players[color]
            if player.socket is not socket:
                return False

            # 입력 형식과 대국 조건을 확인한 뒤 한 번만 적용한다.
            error = None
            try:
                command = Command.model_validate_json(message)
            except ValidationError:
                error = "Invalid request format."
            else:
                try:
                    if command.revision != self._game.revision:
                        raise InvalidAction("The game has changed. Please try again.")
                    if len(self._players) < 2:
                        raise InvalidAction("Please wait for your opponent to join.")
                    if command.type != "resign" and len(self.connected) < 2:
                        raise InvalidAction("Please wait for your opponent to reconnect.")

                    self._game.apply(color, command.type, command.move)
                except InvalidAction as rejection:
                    error = str(rejection)

            # 거절한 요청에도 최신 상태를 보내 두 화면을 맞춘다.
            if error is not None:
                await player.send({"type": "error", "message": error})

            self.last_activity = monotonic()
            await self._broadcast()
            return player.socket is socket

    async def _broadcast(self) -> None:
        # 이 메서드는 방 내부에서만, _lock을 보유한 채 호출한다.
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
            # 전송 중 끊긴 참가자가 있으면 바뀐 접속 상태를 다시 알린다.
