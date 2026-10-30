"""Handle HTTP joins and route Socket.IO events to the relevant room."""

import secrets

from pathlib import Path
from time    import monotonic
from typing  import Annotated

from fastapi             import FastAPI, HTTPException, Path as PathParam
from fastapi.staticfiles import StaticFiles
from pydantic            import BaseModel, ConfigDict, Field, ValidationError
from socketio            import ASGIApp
from socketio.exceptions import ConnectionRefusedError

from .room import Room, RoomFull, sio


api = FastAPI(title = "Small Chess")
app = ASGIApp(sio, api, socketio_path = "api/socket.io", on_shutdown = sio.shutdown)

RoomId = Annotated[str, PathParam(pattern = r"^[a-f0-9]{12}$")]

ROOM_TTL_SECONDS = 24 * 60 * 60
MAX_ROOMS        = 500


class Auth(BaseModel):
    model_config = ConfigDict(extra = "forbid", strict = True)

    id    : str = Field(pattern = r"^[a-f0-9]{12}$")
    token : str = Field(min_length = 1, max_length = 100)


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


@api.get("/api/health")
async def health():
    return {"status": "ok"}


@api.post("/api/games", status_code = 201)
async def create_game():
    clean_rooms()
    if len(rooms) >= MAX_ROOMS:
        raise HTTPException(503, "The server is full. Please try again later.")

    room_id = secrets.token_hex(6)
    while room_id in rooms:
        room_id = secrets.token_hex(6)

    room = Room(room_id)
    rooms[room_id] = room

    return {"id": room_id, "token": room.white_token, "color": "white"}


@api.post("/api/games/{room_id}/join")
async def join_game(room_id: RoomId):
    room = get_room(room_id)

    try:
        token = await room.join()
    except RoomFull as error:
        raise HTTPException(409, str(error)) from None

    return {"id": room_id, "token": token, "color": "black"}


@sio.event
async def connect(sid, environ, auth):
    # Auth travels in the connection payload, keeping tokens out of URLs.
    try:
        auth = Auth.model_validate(auth)
    except ValidationError:
        raise ConnectionRefusedError("Your session is invalid.") from None

    try:
        room = get_room(auth.id)
    except HTTPException as error:
        raise ConnectionRefusedError(error.detail) from None

    color = room.authenticate(auth.token)
    if color is None:
        raise ConnectionRefusedError("Your session is invalid.")

    await sio.save_session(sid, {"room": room, "color": color})
    await room.connect(color, sid)


@sio.event
async def command(sid, data):
    session = await sio.get_session(sid)
    try:
        return await session["room"].handle(session["color"], sid, data)
    except Exception:
        await sio.disconnect(sid)
        raise  # Internal errors must reach the server log, not become game rejections.


@sio.event
async def disconnect(sid, reason):
    session = await sio.get_session(sid)
    if session:
        await session["room"].disconnect(session["color"], sid)


# Serve the built frontend through FastAPI as well. Register it after the API routes.
frontend = Path(__file__).resolve().parents[2] / "frontend" / "dist"

if frontend.is_dir():
    api.mount("/", StaticFiles(directory = frontend, html = True), name = "frontend")
