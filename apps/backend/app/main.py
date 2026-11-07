"""HTTP accounts and local sockets; PostgreSQL connects the servers."""

import asyncio
import logging
import os

from time import monotonic

import psycopg

from fastapi                 import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc          import SQLAlchemyError
from pydantic                import ValidationError
from socketio                import ASGIApp, AsyncServer
from socketio.exceptions     import ConnectionRefusedError
from starlette.concurrency   import run_in_threadpool

from . import account, db, room
from .game     import InvalidAction
from .protocol import Auth, Command, Entry, Health


ORIGIN = os.getenv("CHESS_ORIGIN", "http://127.0.0.1:5174")
log    = logging.getLogger("chess")

api = FastAPI(title = "Small Chess")
api.include_router(account.router)
api.add_middleware(
    CORSMiddleware,
    allow_origins = [ORIGIN],
    allow_methods = ["GET", "POST", "PUT", "DELETE"],
    allow_headers = ["Authorization", "Content-Type"],
)

sio = AsyncServer(
    async_mode           = "asgi",
    async_handlers       = False,
    max_http_buffer_size = 4096,

    cors_allowed_origins = [ORIGIN],
    cors_credentials     = False,
)

# Cache the source of each screen, so unchanged games need no reconstruction.
clients : dict[str, str]           = {}  # socket -> user
sent    : dict[str, room.RoomState] = {}  # socket -> last delivered state
pending : set[str]                 = set()
tasks   : list[asyncio.Task]        = []

changed = asyncio.Event()


def wake(*users: str) -> None:
    pending.update(users)
    changed.set()


async def refresh(sids: list[str]) -> None:
    if not sids:
        return

    states = await run_in_threadpool(room.read, sids)

    for sid in sids:
        if sid not in clients:
            continue

        state = states.get(sid)
        if state is None:
            await sio.emit("session_closed", "Signed out or connected in another tab.", to = sid)
            await sio.disconnect(sid)
        elif state != sent.get(sid):
            view = await run_in_threadpool(room.snapshot, state)
            if sid in clients:
                await sio.emit("state", view.model_dump(), to = sid)
                sent[sid] = state


async def listen() -> None:
    while True:
        try:
            async with await psycopg.AsyncConnection.connect(
                db.URL,
                autocommit      = True,
                connect_timeout = 5,
            ) as connection:
                await connection.execute("LISTEN chess")
                wake(*clients.values())

                while True:
                    async for notice in connection.notifies(timeout = 10, stop_after = 1):
                        wake(notice.payload)

                    await connection.execute("SELECT 1")

        except psycopg.Error:
            log.exception("Database notifications interrupted")
            await asyncio.sleep(1)


async def update() -> None:
    heartbeat_at = monotonic()

    while True:
        try:
            timeout = max(0, heartbeat_at - monotonic())
            try:
                await asyncio.wait_for(changed.wait(), timeout = timeout)
            except TimeoutError:
                pass

            changed.clear()
            users = pending.copy()
            pending.clear()

            if monotonic() >= heartbeat_at:
                await run_in_threadpool(room.heartbeat, list(clients))
                heartbeat_at = monotonic() + 10
                users.update(clients.values())

            # Reconcile on heartbeat too: notifications are not stored history.
            sids = [sid for sid, user in clients.items() if user in users]
            await refresh(sids)

        except SQLAlchemyError:
            log.exception("Could not refresh connected players")
            await asyncio.sleep(1)
            wake(*clients.values())


async def startup() -> None:
    await run_in_threadpool(db.initialize)
    tasks.extend([
        asyncio.create_task(listen()),
        asyncio.create_task(update()),
    ])


async def shutdown() -> None:
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions = True)

    try:
        if clients:
            await run_in_threadpool(room.disconnect, list(clients))
    except SQLAlchemyError:
        log.exception("Could not release connections; their leases will expire")

    await sio.shutdown()
    await run_in_threadpool(db.engine.dispose)


app = ASGIApp(
    sio, api,
    socketio_path = "api/socket.io",
    on_startup    = startup,
    on_shutdown   = shutdown,
)


@api.get("/api/health")
async def health() -> Health:
    if any(task.done() for task in tasks):
        raise HTTPException(503, "Connection updates unavailable.")

    try:
        await run_in_threadpool(db.check)
    except SQLAlchemyError:
        raise HTTPException(503, "Database unavailable.") from None

    return Health(status = "ok")


@sio.event
async def connect(sid, environ, auth):
    try:
        auth = Auth.model_validate(auth or {})
        key  = account.session_key(auth.token)

        user_id = await run_in_threadpool(room.connect, sid, key, auth.id)

    except ValidationError:
        raise ConnectionRefusedError("Invalid session or room.") from None
    except HTTPException as error:
        raise ConnectionRefusedError(error.detail) from None
    except SQLAlchemyError:
        raise ConnectionRefusedError("Server unavailable. Please reconnect.") from None

    clients[sid] = str(user_id)
    wake(str(user_id))


async def act(sid, data, model, action) -> dict[str, str | None]:
    error = None

    try:
        message = model.model_validate(data)
        await run_in_threadpool(action, sid, message)

    except ValidationError:
        error = "Invalid request format."
    except InvalidAction as rejection:
        error = str(rejection)
    except HTTPException as rejection:
        error = rejection.detail
    except SQLAlchemyError:
        log.exception("Could not commit player action")
        return {"error": "Server unavailable. Reconnect to check the latest state."}

    # Rejections may mean the caller has stale state. Successful writes notify at commit.
    if error and sid in clients:
        wake(clients[sid])
    return {"error": error}


@sio.event
async def enter(sid, data):
    return await act(sid, data, Entry, room.enter)


@sio.event
async def command(sid, data):
    return await act(sid, data, Command, room.command)


@sio.event
async def disconnect(sid, reason):
    clients.pop(sid, None)
    sent.pop(sid, None)
    try:
        await run_in_threadpool(room.disconnect, [sid])
    except SQLAlchemyError:
        log.exception("Could not release player connection")
