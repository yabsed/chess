"""Sign in over HTTP; enter, match and play through one Socket.IO connection."""

import asyncio
import secrets

from pathlib import Path
from time    import monotonic

from fastapi               import FastAPI, HTTPException, Request, Response
from fastapi.responses     import JSONResponse
from fastapi.staticfiles   import StaticFiles
from pydantic              import ValidationError
from socketio              import ASGIApp, AsyncServer
from socketio.exceptions   import ConnectionRefusedError
from starlette.concurrency import run_in_threadpool

from . import account
from .protocol import Auth, Entry, Health, User
from .room     import Room, RoomFull


api = FastAPI(title = "Small Chess")
api.include_router(account.router)

sio = AsyncServer(async_mode = "asgi", async_handlers = False, max_http_buffer_size = 4096)
app = ASGIApp(
    sio, api, socketio_path = "api/socket.io",
    on_startup = account.initialize, on_shutdown = sio.shutdown,
)

ROOM_TTL_SECONDS = 24 * 60 * 60
MAX_ROOMS        = 500

rooms   : dict[str, Room] = {}
clients : dict[int, str]  = {}
waiting : dict[int, str]  = {}
lobby_lock = asyncio.Lock()


@api.middleware("http")
async def check_origin(request: Request, call_next):
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        allowed = account.origin_allowed(
            request.headers.get("origin"), request.url.scheme, request.headers["host"],
        )
        if not allowed:
            return JSONResponse({"detail": "Request origin is not allowed."}, status_code = 403)
    return await call_next(request)


@api.get("/api/health")
async def health() -> Health:
    return Health(status = "ok")


@api.delete("/api/session", status_code = 204)
async def logout(request: Request, response: Response):
    response.delete_cookie("chess_session")
    key = account.session_key(request.headers.get("cookie", ""))

    async with lobby_lock:
        user = await run_in_threadpool(account.find_user, key)
        await run_in_threadpool(account.delete_session, key)
        if user is None:
            return

        sid = clients.get(user.id)
        if sid is None:
            return

        session = await sio.get_session(sid)
        if session["key"] != key:
            return

    await sio.emit("session_closed", "You have signed out.", to = sid)
    await sio.disconnect(sid)


@api.put("/api/me/avatar")
async def upload_avatar(request: Request) -> User:
    user = await account.upload_avatar(request)
    for room in list(rooms.values()):
        await room.update_user(user)
    return user


def clean_rooms() -> None:
    now = monotonic()
    for room_id, room in list(rooms.items()):
        if not room.connected and now - room.last_activity > ROOM_TTL_SECONDS:
            del rooms[room_id]


def active_room(user_id: int) -> Room | None:
    for room in rooms.values():
        if not room.finished and room.color_for(user_id) is not None:
            return room
    return None


def new_room(white: User, black: User | None = None) -> Room:
    clean_rooms()
    if len(rooms) >= MAX_ROOMS:
        raise HTTPException(503, "The server is full. Please try again later.")

    room_id = secrets.token_hex(6)
    while room_id in rooms:
        room_id = secrets.token_hex(6)

    room = Room(room_id, sio, white, black)
    rooms[room_id] = room
    return room


async def seat_player(sid: str, session: dict, room: Room | None) -> None:
    user     = session["user"]
    previous = session["room"]
    if previous is not None and previous is not room:
        await previous.disconnect(user.id, sid)

    waiting.pop(user.id, None)
    session["room"] = room
    await sio.save_session(sid, session)

    if room is None:
        await sio.emit("seat", None, to = sid)
    else:
        await room.connect(user, sid)


async def authenticated(sid: str) -> dict:
    try:
        session = await sio.get_session(sid)
    except KeyError:
        raise HTTPException(401, "Your connection has ended.") from None

    user = await run_in_threadpool(account.find_user, session["key"])
    if user is None or clients.get(user.id) != sid or not sio.manager.is_connected(sid, "/"):
        raise HTTPException(401, "Your session has ended. Please sign in again.")
    session["user"] = user
    return session


async def match_player(sid: str, session: dict) -> None:
    user = session["user"]
    for opponent_id, opponent_sid in list(waiting.items()):
        if opponent_id == user.id:
            continue
        try:
            opponent = await authenticated(opponent_sid)
        except HTTPException:
            waiting.pop(opponent_id, None)
            continue

        white, black = user, opponent["user"]
        if secrets.randbelow(2):
            white, black = black, white

        # Reserve both seats before sending either result. Reconnects recover them.
        room = new_room(white, black)
        await seat_player(opponent_sid, opponent, room)
        await seat_player(sid, session, room)
        return

    waiting[user.id] = sid
    await sio.emit("waiting", True, to = sid)


@sio.event
async def connect(sid, environ, auth):
    scope   = environ["asgi.scope"]
    scheme  = "https" if scope["scheme"] in ("https", "wss") else "http"
    allowed = account.origin_allowed(environ.get("HTTP_ORIGIN"), scheme, environ.get("HTTP_HOST", ""))
    if not allowed:
        raise ConnectionRefusedError("Request origin is not allowed.")

    try:
        auth = Auth.model_validate(auth or {})
    except ValidationError:
        raise ConnectionRefusedError("Invalid room.") from None

    key = account.session_key(environ.get("HTTP_COOKIE", ""))
    async with lobby_lock:
        user = await run_in_threadpool(account.find_user, key)
        if user is None:
            raise ConnectionRefusedError("Please sign in again.")

        previous = clients.get(user.id)
        clients[user.id] = sid
        waiting.pop(user.id, None)

        session = {"key": key, "user": user, "room": None}
        await sio.save_session(sid, session)
        clean_rooms()

        room = active_room(user.id)
        if room is None and auth.id is not None:
            room = rooms.get(auth.id)
        if room is not None and room.color_for(user.id) is not None:
            await seat_player(sid, session, room)
        else:
            await sio.emit("seat", None, to = sid)

    # Disconnect calls back into the lobby, so release its lock first.
    if previous is not None:
        await sio.emit("session_closed", "Your account is connected in another tab.", to = previous)
        await sio.disconnect(previous)


@sio.event
async def enter(sid, data):
    try:
        entry = Entry.model_validate(data)
        async with lobby_lock:
            session = await authenticated(sid)
            user    = session["user"]
            clean_rooms()

            room = active_room(user.id)
            if entry.type == "cancel" and (room is None or not room.has_opponent):
                await seat_player(sid, session, None)
                if room is not None:
                    del rooms[room.id]
                await sio.emit("waiting", False, to = sid)
                return {"error": None}

            if room is not None:
                await seat_player(sid, session, room)
                return {"error": None}

            if entry.type == "match":
                await match_player(sid, session)
                return {"error": None}

            if entry.type == "create":
                room = new_room(user)
            else:
                room = rooms.get(entry.id) if entry.id is not None else None
                if room is None:
                    raise HTTPException(404, "This game does not exist or has expired.")
                await room.join(user)

            await seat_player(sid, session, room)
            return {"error": None}

    except ValidationError:
        return {"error": "Invalid request format."}
    except HTTPException as error:
        return {"error": error.detail}
    except RoomFull as error:
        return {"error": str(error)}


@sio.event
async def command(sid, data):
    try:
        session = await authenticated(sid)
        if session["room"] is None:
            return {"error": "Join a game first."}
        return await session["room"].handle(session["user"].id, sid, data)
    except HTTPException as error:
        await sio.emit("session_closed", error.detail, to = sid)
        await sio.disconnect(sid)
        return {"error": error.detail}
    except Exception:
        await sio.disconnect(sid)
        raise


@sio.event
async def disconnect(sid, reason):
    async with lobby_lock:
        session = await sio.get_session(sid)
        if not session:
            return

        user_id = session["user"].id
        if clients.get(user_id) == sid:
            clients.pop(user_id)
        if waiting.get(user_id) == sid:
            waiting.pop(user_id)
        if session["room"] is not None:
            await session["room"].disconnect(user_id, sid)


frontend = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if frontend.is_dir():
    api.mount("/", StaticFiles(directory = frontend, html = True), name = "frontend")
