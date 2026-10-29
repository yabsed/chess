"""Handle joins over HTTP and route incoming WebSocket messages to the relevant room."""

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

    # Keep tokens out of URLs so they do not appear in server URL logs or invitation links.
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
            # Only socket errors end the connection here; let internal room exceptions propagate.
            try:
                message = await socket.receive_text()
            except (WebSocketDisconnect, RuntimeError, OSError):
                break
            except KeyError:  # receive_text received a binary frame.
                await socket.close(1003, "Only JSON text messages are supported.")
                break

            still_connected = await room.handle(color, socket, message)
            if not still_connected:
                break


# Serve the built frontend through FastAPI as well. Register it after the API routes.
frontend = Path(__file__).resolve().parents[2] / "frontend" / "dist"

if frontend.is_dir():
    app.mount("/", StaticFiles(directory = frontend, html = True), name = "frontend")
