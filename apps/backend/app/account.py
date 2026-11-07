"""Accounts, token sessions and small avatars in the shared database."""

import hashlib
import secrets

from io        import BytesIO
from threading import BoundedSemaphore

from fastapi               import APIRouter, Depends, HTTPException, Request, Response
from PIL                   import Image, ImageOps, UnidentifiedImageError
from psycopg.errors        import UniqueViolation
from pwdlib                import PasswordHash
from starlette.concurrency import run_in_threadpool

from . import room
from .db       import lobby_transaction, pool
from .protocol import Credentials, Session, User


router         = APIRouter(prefix = "/api")
password       = PasswordHash.recommended()
password_slots = BoundedSemaphore(8)

MAX_UPLOAD = 2 * 1024 * 1024

dummy_hash = password.hash(secrets.token_urlsafe(32))


def session_key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def request_key(request: Request) -> str:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    return session_key(token) if scheme.lower() == "bearer" else ""


def find_user(key: str) -> User | None:
    with pool.connection() as db:
        row = db.execute("""
            SELECT users.id, users.username, users.avatar_version
            FROM sessions
            JOIN users ON users.id = sessions.user_id
            WHERE sessions.token_hash = %s
              AND sessions.expires_at > now()
        """, (key,)).fetchone()
    return User.profile(row["id"], row["username"], row["avatar_version"]) if row else None


async def current_user(request: Request) -> User:
    key  = request_key(request)
    user = await run_in_threadpool(find_user, key)
    if user is None:
        raise HTTPException(401, "Please sign in again.")
    return user


def login_attempt(request: Request, response: Response) -> None:
    address = request.client.host if request.client else "unknown"
    response.headers["Cache-Control"] = "no-store"

    # Commit the attempt even when password verification later fails.
    with pool.connection() as db:
        db.execute("DELETE FROM attempts WHERE expires_at <= now()")
        row = db.execute("""
            INSERT INTO attempts (address, count, expires_at)
            VALUES (%s, 1, now() + interval '1 minute')
            ON CONFLICT (address) DO UPDATE
            SET count = attempts.count + 1
            RETURNING count
        """, (address,)).fetchone()
    assert row is not None
    if row["count"] > 10:
        raise HTTPException(429, "Too many attempts. Try again in a minute.")


def new_session(db, row: dict) -> Session:
    token = secrets.token_urlsafe(32)
    key   = session_key(token)
    user  = User.profile(row["id"], row["username"], row["avatar_version"])

    db.execute("DELETE FROM sessions WHERE expires_at <= now()")
    db.execute("""
        INSERT INTO sessions (token_hash, user_id)
        VALUES (%s, %s)
    """, (key, user.id))
    return Session(user = user, token = token)


@router.post("/register", status_code = 201, dependencies = [Depends(login_attempt)])
def register(credentials: Credentials) -> Session:
    username = credentials.username.lower()

    # Bound password work without occupying database connections.
    with password_slots:
        hashed = password.hash(credentials.password)

    with pool.connection() as db:
        try:
            row = db.execute("""
                INSERT INTO users (username, password_hash)
                VALUES (%s, %s)
                RETURNING id, username, avatar_version
            """, (username, hashed)).fetchone()
        except UniqueViolation:
            raise HTTPException(409, "That username is already taken.") from None
        assert row is not None
        return new_session(db, row)


@router.post("/session", dependencies = [Depends(login_attempt)])
def login(credentials: Credentials) -> Session:
    with pool.connection() as db:
        row = db.execute("""
            SELECT id, username, password_hash, avatar_version
            FROM users
            WHERE username = %s
        """, (credentials.username.lower(),)).fetchone()

    hashed = row["password_hash"] if row else dummy_hash
    with password_slots:
        valid = password.verify(credentials.password, hashed)
    if not valid or row is None:
        raise HTTPException(401, "Incorrect username or password.")

    with pool.connection() as db:
        return new_session(db, row)


@router.get("/me")
def me(request: Request, response: Response) -> User | None:
    response.headers["Cache-Control"] = "no-store"
    return find_user(request_key(request))


@router.delete("/session", status_code = 204)
def logout(request: Request):
    key = request_key(request)
    with lobby_transaction() as db:
        # Finish any in-flight action in this game before revoking its session.
        db.execute("""
            SELECT room.id
            FROM rooms AS room
            JOIN players AS player ON player.room_id = room.id
            WHERE player.token_hash = %s
            FOR UPDATE OF room
        """, (key,))
        session = db.execute("""
            DELETE FROM sessions
            WHERE token_hash = %s
            RETURNING user_id
        """, (key,)).fetchone()
        if session:
            room.notify(db, session["user_id"])


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
    with pool.connection() as db:
        row = db.execute("""
            UPDATE users
            SET avatar         = %(avatar)s,
                avatar_version = %(avatar_version)s
            WHERE id = %(user_id)s
            RETURNING id, username, avatar_version
        """, {"avatar": data, "avatar_version": version, "user_id": user_id}).fetchone()
        assert row is not None
        room.notify(db, user_id)
    return User.profile(row["id"], row["username"], row["avatar_version"])


@router.put("/me/avatar")
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
    with pool.connection() as db:
        row = db.execute("SELECT avatar FROM users WHERE id = %s", (user_id,)).fetchone()
    if row is None or row["avatar"] is None:
        raise HTTPException(404, "No profile picture.")
    return Response(
        row["avatar"], media_type = "image/webp",
        headers = {"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"},
    )
