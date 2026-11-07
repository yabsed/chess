"""Assemble the API, realtime connections and application lifecycle."""

from fastapi                 import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from socketio                import ASGIApp
from sqlalchemy.exc          import SQLAlchemyError
from starlette.concurrency   import run_in_threadpool

from . import account, db, realtime
from .protocol import Health


api = FastAPI(title = "Small Chess")
api.include_router(account.router)
api.add_middleware(
    CORSMiddleware,
    allow_origins = [realtime.ORIGIN],
    allow_methods = ["GET", "POST", "PUT", "DELETE"],
    allow_headers = ["Authorization", "Content-Type"],
)


async def startup() -> None:
    await run_in_threadpool(db.initialize)
    realtime.start()


async def shutdown() -> None:
    await realtime.stop()
    await run_in_threadpool(db.engine.dispose)


app = ASGIApp(
    realtime.sio, api,
    socketio_path = "api/socket.io",
    on_startup    = startup,
    on_shutdown   = shutdown,
)


@api.get("/api/health")
async def health() -> Health:
    if any(task.done() for task in realtime.tasks):
        raise HTTPException(503, "Connection updates unavailable.")

    try:
        await run_in_threadpool(db.check)
    except SQLAlchemyError:
        raise HTTPException(503, "Database unavailable.") from None

    return Health(status = "ok")
