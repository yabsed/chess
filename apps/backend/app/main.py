"""HTTP로 입장하고 WebSocket으로 메시지를 받아 해당 방에 전달한다."""

import asyncio
import secrets

from pathlib import Path
from time    import monotonic
from typing  import Annotated

from fastapi             import FastAPI, HTTPException, Path as PathParam
from fastapi             import WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic            import BaseModel, ConfigDict, Field, ValidationError

from .room import Room, RoomFull


app = FastAPI(title = "Small Chess")

RoomId = Annotated[str, PathParam(pattern = r"^[a-f0-9]{12}$")]

ROOM_TTL_SECONDS     = 24 * 60 * 60
MAX_ROOMS            = 500
AUTH_TIMEOUT_SECONDS = 5


class Auth(BaseModel):
    model_config = ConfigDict(extra = "forbid", strict = True)

    token: str = Field(min_length = 1, max_length = 100)


rooms: dict[str, Room] = {}


def clean_rooms() -> None:
    now = monotonic()

    for room_id, room in list(rooms.items()):
        inactive_seconds = now - room.last_activity
        if not room.connected and inactive_seconds > ROOM_TTL_SECONDS:
            del rooms[room_id]


def get_room(room_id: str) -> Room:
    clean_rooms()

    if room_id not in rooms:
        raise HTTPException(404, "This game does not exist or has expired. Please start a new game.")

    return rooms[room_id]


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.post("/api/games", status_code = 201)
async def create_game():
    clean_rooms()
    if len(rooms) >= MAX_ROOMS:
        raise HTTPException(503, "The server is full. Please try again later.")

    room_id = secrets.token_hex(6)
    while room_id in rooms:
        room_id = secrets.token_hex(6)

    room = Room()
    rooms[room_id] = room

    return {"id": room_id, "token": room.white_token, "color": "white"}


@app.post("/api/games/{room_id}/join")
async def join_game(room_id: RoomId):
    room = get_room(room_id)

    try:
        token = await room.join()
    except RoomFull as error:
        raise HTTPException(409, str(error)) from None

    return {"id": room_id, "token": token, "color": "black"}


@app.websocket("/api/games/{room_id}/ws")
async def play(socket: WebSocket, room_id: RoomId):
    await socket.accept()

    try:
        room = get_room(room_id)
    except HTTPException:
        await socket.close(4404, "This game does not exist or has expired.")
        return

    # 토큰을 URL에 넣지 않아 서버의 URL 로그나 초대 링크에 남기지 않는다.
    try:
        raw  = await asyncio.wait_for(socket.receive_text(), timeout = AUTH_TIMEOUT_SECONDS)
        auth = Auth.model_validate_json(raw)
    except WebSocketDisconnect:
        return
    except (ValidationError, TimeoutError, KeyError):
        await socket.close(4401, "Your session is invalid.")
        return

    color = room.authenticate(auth.token)
    if color is None:
        await socket.close(4401, "Your session is invalid.")
        return

    async with room.connect(color, socket):
        while True:
            # 소켓 오류만 연결 종료로 다룬다. 방 내부의 예외는 숨기지 않는다.
            try:
                message = await socket.receive_text()
            except (WebSocketDisconnect, RuntimeError, OSError):
                break
            except KeyError:  # receive_text가 바이너리 프레임을 받은 경우.
                await socket.close(1003, "Only JSON text messages are supported.")
                break

            still_connected = await room.handle(color, socket, message)
            if not still_connected:
                break


# 빌드 후에는 FastAPI 하나로 프런트까지 제공한다. API보다 뒤에 등록한다.
frontend = Path(__file__).resolve().parents[2] / "frontend" / "dist"

if frontend.is_dir():
    app.mount("/", StaticFiles(directory = frontend, html = True), name = "frontend")
