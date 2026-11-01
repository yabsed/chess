"""Accounts, token sessions and small avatars share one SQLite file."""

import hashlib
import os
import secrets
import sqlite3

from contextlib import contextmanager
from io         import BytesIO
from pathlib    import Path
from time       import monotonic, time

from fastapi import APIRouter, HTTPException, Request, Response
from PIL     import Image, ImageOps, UnidentifiedImageError
from pwdlib  import PasswordHash
from starlette.concurrency import run_in_threadpool

from .protocol import Credentials, Session, User


router   = APIRouter(prefix = "/api")
password = PasswordHash.recommended()

DATABASE    = Path(os.getenv("CHESS_DB", Path(__file__).resolve().parents[1] / "data/chess.sqlite"))
SESSION_AGE = 7 * 24 * 60 * 60
MAX_UPLOAD  = 2 * 1024 * 1024

attempts: dict[str, tuple[int, float]] = {}
dummy_hash = password.hash(secrets.token_urlsafe(32))


@contextmanager
def database():
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize() -> None:
    DATABASE.parent.mkdir(parents = True, exist_ok = True)
    with database() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id             INTEGER PRIMARY KEY,
                username       TEXT NOT NULL UNIQUE,
                password_hash  TEXT NOT NULL,
                avatar         BLOB,
                avatar_version TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id    INTEGER NOT NULL,
                expires_at INTEGER NOT NULL
            );
        """)


def user_from(row) -> User:
    avatar = None
    if row["avatar_version"]:
        avatar = f"/api/users/{row['id']}/avatar?v={row['avatar_version']}"
    return User(id = row["id"], username = row["username"], avatar = avatar)


def session_key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def request_key(request: Request) -> str:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    return session_key(token) if scheme.lower() == "bearer" else ""


def find_user(key: str) -> User | None:
    with database() as db:
        row = db.execute("""
            SELECT users.id, username, avatar_version
            FROM sessions JOIN users ON users.id = sessions.user_id
            WHERE token_hash = ? AND expires_at > ?
        """, (key, int(time()))).fetchone()
    return user_from(row) if row else None


async def current_user(request: Request) -> User:
    key  = request_key(request)
    user = await run_in_threadpool(find_user, key)
    if user is None:
        raise HTTPException(401, "Please sign in again.")
    return user


def limit_attempts(request: Request) -> None:
    # Called on the event loop before password work goes to a worker thread.
    now = monotonic()
    for address, (_, deadline) in list(attempts.items()):
        if deadline <= now:
            del attempts[address]

    address = request.client.host if request.client else "unknown"
    count, deadline = attempts.get(address, (0, now + 60))
    if count >= 10 or len(attempts) >= 4096:
        raise HTTPException(429, "Too many attempts. Try again in a minute.")
    attempts[address] = (count + 1, deadline)


def sign_in(credentials: Credentials, register: bool) -> tuple[User, str]:
    username = credentials.username.lower()
    with database() as db:
        if register:
            hashed = password.hash(credentials.password)
            try:
                row = db.execute("""
                    INSERT INTO users (username, password_hash) VALUES (?, ?)
                    RETURNING id, username, avatar_version
                """, (username, hashed)).fetchone()
            except sqlite3.IntegrityError:
                raise HTTPException(409, "That username is already taken.") from None
        else:
            row = db.execute("""
                SELECT id, username, password_hash, avatar_version
                FROM users WHERE username = ?
            """, (username,)).fetchone()

            hashed = row["password_hash"] if row else dummy_hash
            valid  = password.verify(credentials.password, hashed)
            if not valid or row is None:
                raise HTTPException(401, "Incorrect username or password.")

        token = secrets.token_urlsafe(32)
        key   = session_key(token)
        now   = int(time())

        db.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        db.execute("INSERT INTO sessions VALUES (?, ?, ?)", (key, row["id"], now + SESSION_AGE))
    return user_from(row), token


@router.post("/register", status_code = 201)
@router.post("/session")
async def login(credentials: Credentials, request: Request, response: Response) -> Session:
    limit_attempts(request)
    register = request.url.path == "/api/register"
    user, token = await run_in_threadpool(sign_in, credentials, register)

    response.headers["Cache-Control"] = "no-store"
    return Session(user = user, token = token)


@router.get("/me")
async def me(request: Request, response: Response) -> User | None:
    response.headers["Cache-Control"] = "no-store"
    key = request_key(request)
    return await run_in_threadpool(find_user, key)


def delete_session(key: str) -> None:
    with database() as db:
        db.execute("DELETE FROM sessions WHERE token_hash = ?", (key,))


def save_avatar(user_id: int, data: bytes) -> User:
    try:
        with Image.open(BytesIO(data), formats = ("JPEG", "PNG", "WEBP")) as source:
            if source.width * source.height > 16_000_000:
                raise ValueError("Too many pixels")

            avatar = ImageOps.exif_transpose(source)
            avatar = avatar.convert("RGB")
            avatar = ImageOps.fit(avatar, (256, 256))

            output = BytesIO()
            avatar.save(output, format = "WEBP", quality = 85)
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise HTTPException(400, "Choose a JPEG, PNG or WebP image under 16 megapixels.") from None

    data    = output.getvalue()
    version = hashlib.sha256(data).hexdigest()[:16]
    with database() as db:
        db.execute("UPDATE users SET avatar = ?, avatar_version = ? WHERE id = ?", (data, version, user_id))
        row = db.execute("SELECT id, username, avatar_version FROM users WHERE id = ?", (user_id,)).fetchone()
    return user_from(row)


async def upload_avatar(request: Request) -> User:
    user = await current_user(request)
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_UPLOAD:
            raise HTTPException(413, "Choose an image smaller than 2 MiB.")
        data.extend(chunk)
    return await run_in_threadpool(save_avatar, user.id, bytes(data))


@router.get("/users/{user_id}/avatar")
def avatar(user_id: int) -> Response:
    with database() as db:
        row = db.execute("SELECT avatar FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None or row["avatar"] is None:
        raise HTTPException(404, "No profile picture.")
    return Response(
        row["avatar"], media_type = "image/webp",
        headers = {"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"},
    )
