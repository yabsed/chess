"""Seat players and commit moves. No room or player belongs to a server."""

import secrets

from fastapi            import HTTPException
from psycopg.types.json import Jsonb

from .db       import lobby, pool
from .game     import Game, InvalidAction
from .protocol import Command, Entry, User


MAX_ROOMS = 500


def notify(db, *users: int) -> None:
    # Wake these users and anyone viewing one of their games, after commit.
    db.execute("""
        SELECT pg_notify('chess', p.user_id::text)
        FROM players p LEFT JOIN rooms r ON p.room_id = r.id
        WHERE p.user_id = ANY(%s) OR r.white_id = ANY(%s) OR r.black_id = ANY(%s)
    """, (list(users), list(users), list(users)))


def authenticated(db, sid: str) -> dict:
    player = db.execute("SELECT * FROM online_players WHERE sid = %s", (sid,)).fetchone()
    if player is None:
        raise HTTPException(401, "Your session has ended. Please reconnect.")
    return player


def expire(db) -> None:
    db.execute("""
        DELETE FROM rooms WHERE updated_at < now() - interval '24 hours'
        AND NOT EXISTS (
            SELECT 1 FROM players
            WHERE room_id = rooms.id AND seen_at > now() - interval '24 hours'
        )
    """)


def connect(sid: str, key: str, invited: str | None) -> int:
    with lobby() as db:
        session = db.execute("""
            SELECT user_id FROM sessions WHERE token_hash = %s AND expires_at > now()
        """, (key,)).fetchone()
        if session is None:
            raise HTTPException(401, "Please sign in again.")

        expire(db)
        user_id = session["user_id"]
        player = db.execute("""
            INSERT INTO players (user_id, sid, token_hash, seen_at)
            VALUES (%s, %s, %s, now())
            ON CONFLICT (user_id) DO UPDATE SET
                sid = EXCLUDED.sid, token_hash = EXCLUDED.token_hash,
                seen_at = now(), waiting_at = NULL
            RETURNING room_id
        """, (user_id, sid, key)).fetchone()
        assert player is not None

        current = db.execute("SELECT * FROM rooms WHERE id = %s FOR UPDATE", (player["room_id"],)).fetchone()
        if current is not None and not current["finished"]:
            db.execute("UPDATE rooms SET updated_at = now() WHERE id = %s", (current["id"],))
        else:
            previous = db.execute("""
                SELECT id FROM rooms WHERE id = %s AND (white_id = %s OR black_id = %s)
            """, (invited, user_id, user_id)).fetchone()
            db.execute("UPDATE players SET room_id = %s WHERE user_id = %s",
                       (previous["id"] if previous else None, user_id))
            if previous:
                db.execute("UPDATE rooms SET updated_at = now() WHERE id = %s", (previous["id"],))

        notify(db, user_id)
        return user_id


def disconnect(sids: list[str]) -> None:
    with lobby() as db:
        # An old socket must never erase the connection that replaced it.
        db.execute("""
            UPDATE rooms SET updated_at = now()
            WHERE id IN (SELECT room_id FROM players WHERE sid = ANY(%s))
        """, (sids,))
        players = db.execute("""
            UPDATE players SET sid = NULL, token_hash = NULL, seen_at = NULL, waiting_at = NULL
            WHERE sid = ANY(%s) RETURNING user_id
        """, (sids,)).fetchall()
        notify(db, *(player["user_id"] for player in players))


def heartbeat(sids: list[str]) -> None:
    with pool.connection() as db:
        db.execute("UPDATE players SET seen_at = now() WHERE sid = ANY(%s)", (sids,))


def create(db, white: int, black: int | None = None) -> str:
    count = db.execute("SELECT count(*) AS total FROM rooms WHERE NOT finished").fetchone()
    assert count is not None
    if count["total"] >= MAX_ROOMS:
        raise HTTPException(503, "The server is full. Please try again later.")

    while True:
        room_id = secrets.token_hex(6)
        inserted = db.execute("""
            INSERT INTO rooms (id, white_id, black_id, game) VALUES (%s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING RETURNING id
        """, (room_id, white, black, Jsonb(Game().record()))).fetchone()
        if inserted:
            return room_id


def enter(sid: str, entry: Entry) -> None:
    with lobby() as db:
        expire(db)

        player  = authenticated(db, sid)
        user_id = player["user_id"]
        current = db.execute("SELECT * FROM rooms WHERE id = %s FOR UPDATE", (player["room_id"],)).fetchone()

        if entry.type == "cancel":
            if current and current["black_id"] is not None and not current["finished"]:
                raise InvalidAction("Resign before leaving an ongoing game.")
            db.execute("UPDATE players SET room_id = NULL, waiting_at = NULL WHERE user_id = %s", (user_id,))
            if current and current["black_id"] is None:
                db.execute("DELETE FROM rooms WHERE id = %s", (current["id"],))
            notify(db, user_id)
            return

        if current is not None and not current["finished"]:
            raise InvalidAction("Leave your current game before starting another.")

        participants = [user_id]

        if entry.type == "match":
            opponent = db.execute("""
                SELECT user_id FROM online_players
                WHERE waiting_at IS NOT NULL AND room_id IS NULL AND user_id <> %s
                ORDER BY waiting_at, user_id LIMIT 1
            """, (user_id,)).fetchone()

            if opponent is None:
                db.execute("""
                    UPDATE players SET room_id = NULL, waiting_at = COALESCE(waiting_at, now())
                    WHERE user_id = %s
                """, (user_id,))
                notify(db, user_id)
                return

            participants.append(opponent["user_id"])
            if secrets.randbelow(2):
                participants.reverse()
            room_id = create(db, *participants)

        elif entry.type == "create":
            room_id = create(db, user_id)

        elif entry.type == "join":
            if entry.id is None:
                raise InvalidAction("Choose a game to join.")
            invited = db.execute("SELECT * FROM rooms WHERE id = %s FOR UPDATE", (entry.id,)).fetchone()
            if invited is None:
                raise HTTPException(404, "This game does not exist or has expired.")
            if invited["black_id"] is not None:
                raise HTTPException(409, "This game already has two players.")

            room_id = invited["id"]
            db.execute("UPDATE rooms SET black_id = %s, updated_at = now() WHERE id = %s", (user_id, room_id))

        else:
            raise InvalidAction("Unknown room action.")

        db.execute("""
            UPDATE players SET room_id = %s, waiting_at = NULL WHERE user_id = ANY(%s)
        """, (room_id, participants))
        notify(db, user_id)


def command(sid: str, command: Command) -> None:
    with pool.connection() as db:
        row = db.execute("""
            SELECT r.* FROM rooms r JOIN players p ON p.room_id = r.id
            WHERE p.sid = %s FOR UPDATE OF r
        """, (sid,)).fetchone()

        # A connection or seat may have changed while we waited for the room.
        player = authenticated(db, sid)
        if row is None or row["id"] != player["room_id"]:
            raise InvalidAction("Join a game first.")

        game  = Game.restore(row["game"])
        color = "white" if player["user_id"] == row["white_id"] else "black"

        if command.revision != game.revision:
            raise InvalidAction("The game has changed. Please try again.")
        if row["black_id"] is None:
            raise InvalidAction("Please wait for your opponent to join.")

        connected = db.execute("SELECT count(*) AS total FROM online_players WHERE room_id = %s", (row["id"],)).fetchone()
        assert connected is not None
        if command.type != "resign" and connected["total"] < 2:
            raise InvalidAction("Please wait for your opponent to reconnect.")

        game.apply(color, command.type, command.move)
        db.execute("""
            UPDATE rooms SET game = %s, finished = %s, updated_at = now() WHERE id = %s
        """, (Jsonb(game.record()), game.outcome is not None, row["id"]))
        notify(db, player["user_id"])


def read(sids: list[str]) -> dict[str, dict]:
    # A single statement sees seating, game and presence from the same DB snapshot.
    with pool.connection() as db:
        rows = db.execute("""
            SELECT p.sid, p.user_id, p.waiting_at, r.id, r.white_id, r.black_id, r.game,
                   w.username AS white_name, w.avatar_version AS white_avatar,
                   b.username AS black_name, b.avatar_version AS black_avatar,
                   wp.user_id IS NOT NULL AS white_online,
                   bp.user_id IS NOT NULL AS black_online
            FROM players p JOIN sessions s ON p.token_hash = s.token_hash AND p.user_id = s.user_id
            LEFT JOIN rooms r ON p.room_id = r.id
            LEFT JOIN users w ON r.white_id = w.id
            LEFT JOIN users b ON r.black_id = b.id
            LEFT JOIN online_players wp ON wp.user_id = w.id AND wp.room_id = r.id
            LEFT JOIN online_players bp ON bp.user_id = b.id AND bp.room_id = r.id
            WHERE p.sid = ANY(%s) AND s.expires_at > now()
        """, (sids,)).fetchall()

    return {row["sid"]: row for row in rows}


def snapshot(row: dict) -> dict:
    seat = None
    game = None

    if row["id"] is not None:
        color = "white" if row["user_id"] == row["white_id"] else "black"
        seat  = {"id": row["id"], "color": color}
        game  = Game.restore(row["game"]).snapshot()

        game["players"]   = {}
        game["connected"] = []

        for color in ("white", "black"):
            if row[f"{color}_id"] is None:
                continue
            user = User.profile(row[f"{color}_id"], row[f"{color}_name"], row[f"{color}_avatar"])
            game["players"][color] = user.model_dump()
            if row[f"{color}_online"]:
                game["connected"].append(color)

    return {"seat": seat, "waiting": row["waiting_at"] is not None, "game": game}
