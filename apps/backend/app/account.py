"""Accounts, token sessions and small avatars in the shared database."""

import hashlib
import secrets

from io import BytesIO

from fastapi               import APIRouter, HTTPException, Request, Response
from PIL                   import Image, ImageOps, UnidentifiedImageError
from psycopg.errors        import UniqueViolation
from pwdlib                import PasswordHash
from starlette.concurrency import run_in_threadpool

from .db       import pool, transaction
from .protocol import Credentials, Session, User


router   = APIRouter(prefix = "/api")
password = PasswordHash.recommended()

MAX_UPLOAD = 2 * 1024 * 1024

dummy_hash = password.hash(secrets.token_urlsafe(32))


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
    with pool.connection() as db:
        row = db.execute("""
            SELECT users.id, username, avatar_version
            FROM sessions JOIN users ON users.id = sessions.user_id
            WHERE token_hash = %s AND expires_at > now()
        """, (key,)).fetchone()
    return user_from(row) if row else None


async def current_user(request: Request) -> User:
    key  = request_key(request)
    user = await run_in_threadpool(find_user, key)
    if user is None:
        raise HTTPException(401, "Please sign in again.")
    return user


def limit_attempts(address: str) -> None:
    # Commit the attempt even when password verification later fails.
    with pool.connection() as db:
        db.execute("DELETE FROM attempts WHERE expires_at <= now()")
        row = db.execute("""
            INSERT INTO attempts VALUES (%s, 1, now() + interval '1 minute')
            ON CONFLICT (address) DO UPDATE SET count = attempts.count + 1
            RETURNING count
        """, (address,)).fetchone()
    assert row is not None
    if row["count"] > 10:
        raise HTTPException(429, "Too many attempts. Try again in a minute.")


def sign_in(credentials: Credentials, register: bool) -> tuple[User, str]:
    username = credentials.username.lower()
    with pool.connection() as db:
        if register:
            hashed = password.hash(credentials.password)
            try:
                row = db.execute("""
                    INSERT INTO users (username, password_hash) VALUES (%s, %s)
                    RETURNING id, username, avatar_version
                """, (username, hashed)).fetchone()
            except UniqueViolation:
                raise HTTPException(409, "That username is already taken.") from None
            assert row is not None
        else:
            row = db.execute("""
                SELECT id, username, password_hash, avatar_version
                FROM users WHERE username = %s
            """, (username,)).fetchone()

            hashed = row["password_hash"] if row else dummy_hash
            valid  = password.verify(credentials.password, hashed)
            if not valid or row is None:
                raise HTTPException(401, "Incorrect username or password.")

        token = secrets.token_urlsafe(32)
        key   = session_key(token)
        db.execute("DELETE FROM sessions WHERE expires_at <= now()")
        db.execute("INSERT INTO sessions (token_hash, user_id) VALUES (%s, %s)", (key, row["id"]))
    return user_from(row), token


@router.post("/register", status_code = 201)
@router.post("/session")
async def login(credentials: Credentials, request: Request, response: Response) -> Session:
    address = request.client.host if request.client else "unknown"
    await run_in_threadpool(limit_attempts, address)
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
    with transaction(lobby = True) as db:
        db.execute("DELETE FROM sessions WHERE token_hash = %s", (key,))


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
    with transaction() as db:
        db.execute("UPDATE users SET avatar = %s, avatar_version = %s WHERE id = %s", (data, version, user_id))
        row = db.execute("SELECT id, username, avatar_version FROM users WHERE id = %s", (user_id,)).fetchone()
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
    with pool.connection() as db:
        row = db.execute("SELECT avatar FROM users WHERE id = %s", (user_id,)).fetchone()
    if row is None or row["avatar"] is None:
        raise HTTPException(404, "No profile picture.")
    return Response(
        row["avatar"], media_type = "image/webp",
        headers = {"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"},
    )
