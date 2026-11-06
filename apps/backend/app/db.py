"""Shared storage and the lock for seating players across servers."""

import os

from contextlib import contextmanager
from pathlib    import Path

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


URL = os.environ["CHESS_DATABASE_URL"]

pool = ConnectionPool(
    URL, open = False, min_size = 1, max_size = 8, timeout = 5,
    kwargs = {"row_factory": dict_row, "connect_timeout": 5},
)


def initialize() -> None:
    pool.open(wait = True, timeout = 10)
    with pool.connection() as db:
        # Two newly deployed tasks may start at the same time.
        db.execute("SELECT pg_advisory_xact_lock(0)")
        db.execute(Path(__file__).with_name("schema.sql").read_bytes())


@contextmanager
def lobby():
    with pool.connection() as db:
        # Serialize seating and room capacity. Moves only lock their room row.
        db.execute("SELECT pg_advisory_xact_lock(1)")
        yield db


def check() -> None:
    with pool.connection() as db:
        db.execute("SELECT id FROM rooms LIMIT 1")
