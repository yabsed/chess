"""Handle HTTP joins and route Socket.IO events to the relevant room."""

import secrets

from pathlib import Path
from time    import monotonic

from fastapi             import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic            import ValidationError
from socketio            import ASGIApp, AsyncServer
from socketio.exceptions import ConnectionRefusedError

from .protocol import ApiError, Auth, Health, RoomId, Session
from .room     import Room, RoomFull


api = FastAPI(
    title       = "Small Chess",
    description = "Create a game and reserve a seat over HTTP. "
                  "Game commands and live state use Socket.IO at /api/socket.io.",
)

sio = AsyncServer(
    async_mode           = "asgi",
    async_handlers       = False,
    max_http_buffer_size = 4096,
)

app = ASGIApp(sio, api, socketio_path = "api/socket.io", on_shutdown = sio.shutdown)

ROOM_TTL_SECONDS = 24 * 60 * 60
MAX_ROOMS        = 500

rooms: dict[str, Room] = {}


def clean_rooms() -> None:
    now = monotonic()

    for room_id, room in list(rooms.items()):
        inactive_seconds = now - room.last_activity
        if not room.connected and inactive_seconds > ROOM_TTL_SECONDS:
            del rooms[room_id]


def find_room(room_id: str) -> Room | None:
    clean_rooms()
    return rooms.get(room_id)


@api.get("/api/health")
async def health() -> Health:
    """Check whether the server is running."""
    return Health(status = "ok")


@api.post(
    "/api/games",
    status_code = 201,
    responses   = {503: {"model": ApiError, "description": "The server has reached its room limit."}},
)
async def create_game() -> Session:
    """Create a game and reserve the white seat."""
    clean_rooms()
    if len(rooms) >= MAX_ROOMS:
        raise HTTPException(503, "The server is full. Please try again later.")

    room_id = secrets.token_hex(6)
    while room_id in rooms:
        room_id = secrets.token_hex(6)

    room = Room(room_id, sio)
    rooms[room_id] = room

    return Session(
        id    = room_id,
        token = room.white_token,
        color = "white",
    )


@api.post(
    "/api/games/{room_id}/join",
    responses = {
        404: {"model": ApiError, "description": "The game does not exist or has expired."},
        409: {"model": ApiError, "description": "Both seats are already reserved."},
    },
)
async def join_game(room_id: RoomId) -> Session:
    """Reserve the black seat in an existing game."""
    room = find_room(room_id)
    if room is None:
        raise HTTPException(404, "This game does not exist or has expired. Please start a new game.")

    try:
        token = await room.join()
    except RoomFull as error:
        raise HTTPException(409, str(error)) from None

    return Session(
        id    = room_id,
        token = token,
        color = "black",
    )


@sio.event
async def connect(sid, environ, auth):
    # Auth travels in the connection payload, keeping tokens out of URLs.
    try:
        auth = Auth.model_validate(auth)
    except ValidationError:
        raise ConnectionRefusedError("Your session is invalid.") from None

    room = find_room(auth.id)
    if room is None:
        raise ConnectionRefusedError("This game does not exist or has expired. Please start a new game.")

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
