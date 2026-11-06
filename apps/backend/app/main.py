"""HTTP accounts and local sockets; PostgreSQL connects the servers."""

import asyncio
import logging
import os

from time import monotonic

import psycopg

from fastapi                 import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from psycopg_pool            import PoolTimeout
from pydantic                import ValidationError
from socketio                import ASGIApp, AsyncServer
from socketio.exceptions     import ConnectionRefusedError
from starlette.concurrency   import run_in_threadpool

from . import account, db, room
from .game     import InvalidAction
from .protocol import Auth, Command, Entry, Health, User


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
    async_mode = "asgi", async_handlers = False, max_http_buffer_size = 4096,
    cors_allowed_origins = [ORIGIN], cors_credentials = False,
)

# Only socket handles and the last screen sent to each socket live in this process.
clients : set[str]           = set()
sent    : dict[str, dict]    = {}
tasks   : list[asyncio.Task] = []

changed      = asyncio.Event()
refresh_lock = asyncio.Lock()


async def refresh() -> None:
    async with refresh_lock:
        sids = list(clients)
        if not sids:
            return
        views = await run_in_threadpool(room.views, sids)

        for sid in sids:
            if sid not in clients:
                continue
            view = views.get(sid)
            if view is None:
                await sio.emit("session_closed", "Signed out or connected in another tab.", to = sid)
                await sio.disconnect(sid)
            elif view != sent.get(sid):
                await sio.emit("state", view, to = sid)
                sent[sid] = view


async def listen() -> None:
    while True:
        try:
            async with await psycopg.AsyncConnection.connect(
                db.URL, autocommit = True, connect_timeout = 5,
            ) as connection:
                await connection.execute("LISTEN chess")
                changed.set()
                while True:
                    async for _ in connection.notifies(timeout = 10, stop_after = 1):
                        changed.set()
                    await connection.execute("SELECT 1")
        except psycopg.Error:
            log.exception("Database notifications interrupted")
            await asyncio.sleep(1)


async def update() -> None:
    heartbeat_at = monotonic()
    while True:
        try:
            try:
                await asyncio.wait_for(changed.wait(), timeout = max(0, heartbeat_at - monotonic()))
            except TimeoutError:
                pass
            changed.clear()

            if monotonic() >= heartbeat_at:
                if clients:
                    await run_in_threadpool(room.heartbeat, list(clients))
                heartbeat_at = monotonic() + 10

            # Also refresh every heartbeat: NOTIFY is a wake-up, not stored history.
            await refresh()
        except (psycopg.Error, PoolTimeout):
            log.exception("Could not refresh connected players")
            await asyncio.sleep(1)
            changed.set()


async def startup() -> None:
    await run_in_threadpool(db.initialize)
    tasks.extend([asyncio.create_task(listen()), asyncio.create_task(update())])


async def shutdown() -> None:
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions = True)
    try:
        if clients:
            await run_in_threadpool(room.disconnect, list(clients))
    except (psycopg.Error, PoolTimeout):
        log.exception("Could not release connections; their leases will expire")
    await sio.shutdown()
    await run_in_threadpool(db.pool.close)


app = ASGIApp(sio, api, socketio_path = "api/socket.io", on_startup = startup, on_shutdown = shutdown)


@api.get("/api/health")
async def health() -> Health:
    if any(task.done() for task in tasks):
        raise HTTPException(503, "Connection updates unavailable.")
    try:
        await run_in_threadpool(db.check)
    except (psycopg.Error, PoolTimeout):
        raise HTTPException(503, "Database unavailable.") from None
    return Health(status = "ok")


@api.delete("/api/session", status_code = 204)
async def logout(request: Request):
    await run_in_threadpool(account.delete_session, account.request_key(request))


@api.put("/api/me/avatar")
async def upload_avatar(request: Request) -> User:
    return await account.upload_avatar(request)


@sio.event
async def connect(sid, environ, auth):
    try:
        auth = Auth.model_validate(auth or {})
        await run_in_threadpool(room.connect, sid, account.session_key(auth.token), auth.id)
    except ValidationError:
        raise ConnectionRefusedError("Invalid session or room.") from None
    except HTTPException as error:
        raise ConnectionRefusedError(error.detail) from None
    except (psycopg.Error, PoolTimeout):
        raise ConnectionRefusedError("Server unavailable. Please reconnect.") from None

    clients.add(sid)
    await refresh()


async def act(sid, data, model, action) -> dict:
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
    except (psycopg.Error, PoolTimeout):
        log.exception("Could not commit player action")
        return {"error": "Server unavailable. Reconnect to check the latest state."}

    await refresh()
    return {"error": error}


@sio.event
async def enter(sid, data):
    return await act(sid, data, Entry, room.enter)


@sio.event
async def command(sid, data):
    return await act(sid, data, Command, room.command)


@sio.event
async def disconnect(sid, reason):
    clients.discard(sid)
    sent.pop(sid, None)
    try:
        await run_in_threadpool(room.disconnect, [sid])
    except (psycopg.Error, PoolTimeout):
        log.exception("Could not release player connection")
