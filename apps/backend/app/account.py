"""Accounts, token sessions and small avatars in the shared database."""

import hashlib
import secrets

from datetime  import timedelta
from io        import BytesIO
from threading import BoundedSemaphore

from fastapi               import APIRouter, Depends, HTTPException, Request, Response
from PIL                   import Image, ImageOps, UnidentifiedImageError
from psycopg.errors        import UniqueViolation
from pwdlib                import PasswordHash
from starlette.concurrency import run_in_threadpool

from sqlalchemy                    import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc                import IntegrityError
from sqlalchemy.orm                import Session, undefer

from .db       import lobby_transaction, notify, sessions
from .models   import LoginAttempt, LoginSession, Player, Room, User
from .protocol import Credentials, Profile, SessionResponse


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


def find_user(key: str) -> Profile | None:
    with sessions() as db:
        user = db.scalar(
            select(User)
            .join(LoginSession, LoginSession.user_id == User.id)
            .where(
                LoginSession.token_hash == key,
                LoginSession.expires_at > func.now(),
            )
        )
        if user is None:
            return None
        return Profile.for_user(user.id, user.username, user.avatar_version)


async def current_user(request: Request) -> Profile:
    key  = request_key(request)
    user = await run_in_threadpool(find_user, key)
    if user is None:
        raise HTTPException(401, "Please sign in again.")
    return user


def login_attempt(request: Request, response: Response) -> None:
    address = request.client.host if request.client else "unknown"
    response.headers["Cache-Control"] = "no-store"

    # Commit the attempt even when password verification later fails.
    with sessions.begin() as db:
        db.execute(delete(LoginAttempt).where(LoginAttempt.expires_at <= func.now()))

        statement = (
            insert(LoginAttempt)
            .values(
                address    = address,
                count      = 1,
                expires_at = func.now() + timedelta(minutes = 1),
            )
            .on_conflict_do_update(
                index_elements = [LoginAttempt.address],
                set_ = {"count": LoginAttempt.count + 1},
            )
            .returning(LoginAttempt.count)
        )
        count = db.execute(statement).scalar_one()

    if count > 10:
        raise HTTPException(429, "Too many attempts. Try again in a minute.")


def new_session(db: Session, profile: Profile) -> SessionResponse:
    token = secrets.token_urlsafe(32)
    key   = session_key(token)

    db.execute(delete(LoginSession).where(LoginSession.expires_at <= func.now()))
    db.add(LoginSession(token_hash = key, user_id = profile.id))

    return SessionResponse(user = profile, token = token)


@router.post("/register", status_code = 201, dependencies = [Depends(login_attempt)])
def register(credentials: Credentials) -> SessionResponse:
    username = credentials.username.lower()

    # Bound password work without occupying database connections.
    with password_slots:
        hashed = password.hash(credentials.password)

    try:
        with sessions.begin() as db:
            user = User(username = username, password_hash = hashed)
            db.add(user)
            db.flush()

            profile = Profile.for_user(user.id, user.username, user.avatar_version)
            return new_session(db, profile)

    except IntegrityError as error:
        if not isinstance(error.orig, UniqueViolation):
            raise
        raise HTTPException(409, "That username is already taken.") from None


@router.post("/session", dependencies = [Depends(login_attempt)])
def login(credentials: Credentials) -> SessionResponse:
    username = credentials.username.lower()

    with sessions() as db:
        user = db.scalar(
            select(User)
            .where(User.username == username)
            .options(undefer(User.password_hash))
        )

        hashed  = dummy_hash
        profile = None
        if user:
            hashed  = user.password_hash
            profile = Profile.for_user(user.id, user.username, user.avatar_version)

    with password_slots:
        valid = password.verify(credentials.password, hashed)
    if not valid or profile is None:
        raise HTTPException(401, "Incorrect username or password.")

    with sessions.begin() as db:
        return new_session(db, profile)


@router.get("/me")
def me(request: Request, response: Response) -> Profile | None:
    response.headers["Cache-Control"] = "no-store"
    return find_user(request_key(request))


@router.delete("/session", status_code = 204)
def logout(request: Request):
    key = request_key(request)

    with lobby_transaction() as db:
        # Finish any in-flight action in this game before revoking its session.
        db.execute(
            select(Room.id)
            .join(Player, Player.room_id == Room.id)
            .where(Player.token_hash == key)
            .with_for_update(of = Room)
        ).all()

        user_id = db.scalar(
            delete(LoginSession)
            .where(LoginSession.token_hash == key)
            .returning(LoginSession.user_id)
        )
        if user_id is not None:
            notify(db)


def save_avatar(user_id: int, data: bytes) -> Profile:
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

    with sessions.begin() as db:
        user = db.get(User, user_id)
        assert user is not None
        user.avatar         = data
        user.avatar_version = version
        notify(db)
        return Profile.for_user(user.id, user.username, user.avatar_version)


@router.put("/me/avatar")
async def upload_avatar(request: Request) -> Profile:
    user = await current_user(request)
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_UPLOAD:
            raise HTTPException(413, "Choose an image smaller than 2 MiB.")
        data.extend(chunk)
    return await run_in_threadpool(save_avatar, user.id, bytes(data))


@router.get("/users/{user_id}/avatar")
def avatar(user_id: int) -> Response:
    with sessions() as db:
        data = db.scalar(select(User.avatar).where(User.id == user_id))

    if data is None:
        raise HTTPException(404, "No profile picture.")

    return Response(
        data,
        media_type = "image/webp",
        headers = {
            "Cache-Control":          "private, max-age=86400",
            "X-Content-Type-Options": "nosniff",
        },
    )
