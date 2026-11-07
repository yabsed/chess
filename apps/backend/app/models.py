"""PostgreSQL tables. Relationships are queried explicitly by each operation."""

from datetime import datetime

from sqlalchemy import (
    BigInteger, CheckConstraint, DateTime, ForeignKey,
    Identity, Index, Integer, Text, false, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    type_annotation_map = {
        int:      BigInteger,
        str:      Text,
        datetime: DateTime(timezone = True),
    }


class User(Base):
    __tablename__ = "users"

    id             : Mapped[int] = mapped_column(Identity(), primary_key = True)
    username       : Mapped[str] = mapped_column(unique = True)
    password_hash  : Mapped[str] = mapped_column(deferred = True)
    avatar         : Mapped[bytes | None] = mapped_column(deferred = True)
    avatar_version : Mapped[str] = mapped_column(server_default = "")


class LoginSession(Base):
    __tablename__ = "sessions"

    token_hash: Mapped[str] = mapped_column(primary_key = True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete = "CASCADE"),
    )
    expires_at: Mapped[datetime] = mapped_column(
        server_default = text("now() + interval '7 days'"),
    )


class Room(Base):
    __tablename__ = "rooms"
    __table_args__ = (CheckConstraint("white_id <> black_id"),)

    id         : Mapped[str] = mapped_column(primary_key = True)
    white_id   : Mapped[int] = mapped_column(ForeignKey("users.id"))
    black_id   : Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    game       : Mapped[dict[str, object]] = mapped_column(JSONB)
    finished   : Mapped[bool] = mapped_column(server_default = false())
    updated_at : Mapped[datetime] = mapped_column(server_default = func.now())


class Player(Base):
    __tablename__ = "players"
    __table_args__ = (
        Index("players_room", "room_id"),
        Index("players_waiting", "waiting_at",
              postgresql_where = text("waiting_at IS NOT NULL")),
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete = "CASCADE"),
        primary_key = True,
    )
    room_id: Mapped[str | None] = mapped_column(
        ForeignKey("rooms.id", ondelete = "SET NULL"),
    )
    token_hash: Mapped[str | None] = mapped_column(
        ForeignKey("sessions.token_hash", ondelete = "SET NULL"),
    )

    sid        : Mapped[str | None] = mapped_column(unique = True)
    seen_at    : Mapped[datetime | None]
    waiting_at : Mapped[datetime | None]


class LoginAttempt(Base):
    __tablename__ = "attempts"

    address    : Mapped[str] = mapped_column(primary_key = True)
    count      : Mapped[int] = mapped_column(Integer)
    expires_at : Mapped[datetime]
