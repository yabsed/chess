"""Import a stopped server's SQLite backup into an empty PostgreSQL database."""

import argparse
import sqlite3

from datetime import datetime, timezone
from pathlib  import Path

from .db import initialize, pool, transaction


def import_accounts(path: Path) -> tuple[int, int]:
    source = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri = True)
    try:
        source.execute("BEGIN")
        users    = source.execute("SELECT id, username, password_hash, avatar, avatar_version FROM users").fetchall()
        sessions = source.execute("SELECT token_hash, user_id, expires_at FROM sessions").fetchall()
    finally:
        source.close()

    with transaction(lobby = True) as db:
        db.execute("LOCK TABLE users, sessions IN EXCLUSIVE MODE")
        if db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            raise ValueError("The destination already has accounts. Import into an empty database.")

        with db.cursor() as cursor:
            cursor.executemany("""
                INSERT INTO users (id, username, password_hash, avatar, avatar_version)
                VALUES (%s, %s, %s, %s, %s)
            """, users)
            cursor.executemany("""
                INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (%s, %s, %s)
            """, [(key, user_id, datetime.fromtimestamp(expiry, timezone.utc)) for key, user_id, expiry in sessions])

        db.execute("DELETE FROM sessions WHERE expires_at <= now()")
        db.execute("""
            SELECT setval(pg_get_serial_sequence('users', 'id'),
                          COALESCE((SELECT max(id) FROM users), 1),
                          EXISTS (SELECT 1 FROM users))
        """)
        row = db.execute("SELECT count(*) AS total FROM sessions").fetchone()
        assert row is not None
        kept = row["total"]
    return len(users), kept


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description = __doc__)
    parser.add_argument("backup", type = Path)
    args = parser.parse_args()

    initialize()
    try:
        users, sessions = import_accounts(args.backup)
        print(f"Imported {users} accounts and {sessions} unexpired sessions.")
    finally:
        pool.close()
