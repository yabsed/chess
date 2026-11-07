"""Database connections, transactions and committed-change notifications."""

import os

from contextlib import contextmanager

from sqlalchemy        import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm    import Session, sessionmaker

from .models import Base, Room


URL = os.environ["CHESS_DATABASE_URL"]

engine = create_engine(
    make_url(URL).set(drivername = "postgresql+psycopg"),
    pool_size     = 8,
    max_overflow  = 0,
    pool_timeout  = 5,
    pool_pre_ping = True,
    connect_args  = {"connect_timeout": 5},
)

sessions = sessionmaker(engine)


def initialize() -> None:
    with engine.begin() as connection:
        # Two newly deployed tasks may start at the same time.
        connection.execute(text("SELECT pg_advisory_xact_lock(0)"))
        Base.metadata.create_all(connection)


@contextmanager
def lobby_transaction():
    with sessions.begin() as db:
        # Serialize seating and room capacity. Moves only lock their room row.
        db.execute(text("SELECT pg_advisory_xact_lock(1)"))
        yield db


def notify(db: Session) -> None:
    # PostgreSQL delivers this signal only if the transaction commits.
    db.execute(text("NOTIFY chess"))


def check() -> None:
    with engine.connect() as connection:
        connection.execute(select(Room.id).limit(1))
