"""Socket requests and delivery of committed state to local connections."""

import asyncio
import logging
import os

import psycopg

from fastapi               import HTTPException
from pydantic              import ValidationError
from socketio              import AsyncServer
from socketio.exceptions   import ConnectionRefusedError
from sqlalchemy.exc        import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from . import account, db, room
from .game     import InvalidAction
from .protocol import Auth, Command, Entry


ORIGIN = os.getenv("CHESS_ORIGIN", "http://127.0.0.1:5174")
log    = logging.getLogger("chess")

sio = AsyncServer(
    async_mode           = "asgi",
    async_handlers       = False,
    max_http_buffer_size = 4096,

    cors_allowed_origins = [ORIGIN],
    cors_credentials     = False,
)

# Each socket keeps its last delivered state; None means its first screen is due.
connections: dict[str, room.RoomState | None] = {}
changed = asyncio.Event()
tasks: list[asyncio.Task] = []


def start() -> None:
    tasks.extend([
        asyncio.create_task(listen()),
        asyncio.create_task(heartbeat()),
        asyncio.create_task(publish()),
    ])


async def stop() -> None:
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions = True)

    try:
        if connections:
            await run_in_threadpool(room.disconnect, list(connections))
    except SQLAlchemyError:
        log.exception("Could not release connections; their leases will expire")

    connections.clear()
    changed.clear()
    tasks.clear()
    await sio.shutdown()


async def listen() -> None:
    while True:
        try:
            async with await psycopg.AsyncConnection.connect(
                db.URL,
                autocommit      = True,
                connect_timeout = 5,
            ) as connection:
                await connection.execute("LISTEN chess")
                changed.set()

                while True:
                    async for _ in connection.notifies(timeout = 10):
                        changed.set()

                    await connection.execute("SELECT 1")

        except psycopg.Error:
            log.exception("Database notifications interrupted")
            await asyncio.sleep(1)


async def heartbeat() -> None:
    while True:
        try:
            await run_in_threadpool(room.heartbeat, list(connections))
            # Reconcile periodically too: a disconnected listener misses signals.
            changed.set()
        except SQLAlchemyError:
            log.exception("Could not renew player connections")

        await asyncio.sleep(room.HEARTBEAT_SECONDS)


async def publish() -> None:
    while True:
        await changed.wait()
        changed.clear()

        try:
            await refresh()
        except SQLAlchemyError:
            log.exception("Could not refresh connected players")
            await asyncio.sleep(1)
            changed.set()


async def refresh() -> None:
    sids = list(connections)
    if not sids:
        return

    states = await run_in_threadpool(room.read, sids)

    for sid in sids:
        if sid not in connections:
            continue

        state = states.get(sid)
        if state is None:
            message = "Signed out or connected in another tab."
            await sio.emit("session_closed", message, to = sid)
            await sio.disconnect(sid)
            continue

        if state == connections[sid]:
            continue

        view = await run_in_threadpool(room.snapshot, state)
        if sid not in connections:
            continue

        await sio.emit("state", view.model_dump(), to = sid)

        # The socket may have closed while the screen was being sent.
        if sid in connections:
            connections[sid] = state


@sio.event
async def connect(sid, environ, auth):
    try:
        auth = Auth.model_validate(auth or {})
        key  = account.session_key(auth.token)

        await run_in_threadpool(room.connect, sid, key, auth.id)

    except ValidationError:
        raise ConnectionRefusedError("Invalid session or room.") from None
    except HTTPException as error:
        raise ConnectionRefusedError(error.detail) from None
    except SQLAlchemyError:
        raise ConnectionRefusedError("Server unavailable. Please reconnect.") from None

    connections[sid] = None
    # The commit signal may have arrived before this socket was registered.
    changed.set()


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

    # Rejections may mean the caller has stale state. Writes notify at commit.
    if error and sid in connections:
        changed.set()
    return {"error": error}


@sio.event
async def enter(sid, data):
    return await act(sid, data, Entry, room.enter)


@sio.event
async def command(sid, data):
    return await act(sid, data, Command, room.command)


@sio.event
async def disconnect(sid, reason):
    connections.pop(sid, None)

    try:
        await run_in_threadpool(room.disconnect, [sid])
    except SQLAlchemyError:
        log.exception("Could not release player connection")
