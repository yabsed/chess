"""Seat players and commit moves. No room or player belongs to a server."""

import secrets

from fastapi            import HTTPException
from psycopg.types.json import Jsonb

from .db       import lobby_transaction, pool
from .game     import Game, InvalidAction
from .protocol import Command, Entry, User


MAX_ROOMS = 500


def notify(db, *user_ids: int) -> None:
    # Wake these users and anyone viewing one of their games, after commit.
    users = list(user_ids)

    db.execute(t"""
        SELECT pg_notify('chess', player.user_id::text)
        FROM players AS player
        LEFT JOIN rooms AS room ON room.id = player.room_id
        WHERE player.user_id = ANY({users})
           OR room.white_id = ANY({users})
           OR room.black_id = ANY({users})
    """)


def authenticated(db, sid: str) -> dict:
    player = db.execute(t"""
        SELECT *
        FROM online_players
        WHERE sid = {sid}
    """).fetchone()

    if player is None:
        raise HTTPException(401, "Your session has ended. Please reconnect.")
    return player


def expire(db) -> None:
    # Lobby operations hold the seating lock; moves also need the room lock.
    abandoned = db.execute("""
        SELECT
            room.*,
            white.user_id IS NOT NULL AS white_absent,
            black.user_id IS NOT NULL AS black_absent

        FROM rooms AS room

        LEFT JOIN players AS white
            ON white.user_id = room.white_id
           AND white.room_id = room.id
           AND (white.seen_at IS NULL OR white.seen_at <= now() - interval '60 seconds')

        LEFT JOIN players AS black
            ON black.user_id = room.black_id
           AND black.room_id = room.id
           AND (black.seen_at IS NULL OR black.seen_at <= now() - interval '60 seconds')

        WHERE NOT room.finished
          AND (white.user_id IS NOT NULL OR black.user_id IS NOT NULL)
        ORDER BY room.id
        FOR UPDATE OF room
    """).fetchall()

    for room in abandoned:
        room_id = room["id"]

        if room["black_id"] is None:
            db.execute(t"DELETE FROM rooms WHERE id = {room_id}")

        else:
            loser = None
            if not (room["white_absent"] and room["black_absent"]):
                loser = "white" if room["white_absent"] else "black"

            game = Game.restore(room["game"])
            game.abandon(loser)
            record = Jsonb(game.record())

            db.execute(t"""
                UPDATE rooms
                SET game       = {record},
                    finished   = true,
                    updated_at = now()
                WHERE id = {room_id}
            """)

        notify(db, room["white_id"])

    db.execute("""
        DELETE FROM rooms
        WHERE updated_at < now() - interval '24 hours'
          AND NOT EXISTS (
              SELECT 1
              FROM players
              WHERE room_id = rooms.id
                AND seen_at > now() - interval '24 hours'
          )
    """)


def connect(sid: str, key: str, invited_room_id: str | None) -> int:
    with lobby_transaction() as db:
        session = db.execute(t"""
            SELECT user_id
            FROM sessions
            WHERE token_hash = {key}
              AND expires_at > now()
        """).fetchone()

        if session is None:
            raise HTTPException(401, "Please sign in again.")

        expire(db)
        user_id = session["user_id"]

        player = db.execute(t"""
            INSERT INTO players (user_id, sid, token_hash, seen_at)
            VALUES ({user_id}, {sid}, {key}, now())
            ON CONFLICT (user_id) DO UPDATE
            SET sid        = EXCLUDED.sid,
                token_hash = EXCLUDED.token_hash,
                seen_at    = now(),
                waiting_at = NULL
            RETURNING room_id
        """).fetchone()

        assert player is not None
        room_id = player["room_id"]

        current_room = db.execute(t"""
            SELECT *
            FROM rooms
            WHERE id = {room_id}
            FOR UPDATE
        """).fetchone()

        if current_room is not None and not current_room["finished"]:
            db.execute(t"""
                UPDATE rooms
                SET updated_at = now()
                WHERE id = {room_id}
            """)

        else:
            previous_room = db.execute(t"""
                SELECT id
                FROM rooms
                WHERE id = {invited_room_id}
                  AND (white_id = {user_id} OR black_id = {user_id})
            """).fetchone()

            room_id = previous_room["id"] if previous_room else None

            db.execute(t"""
                UPDATE players
                SET room_id = {room_id}
                WHERE user_id = {user_id}
            """)

            if previous_room:
                db.execute(t"""
                    UPDATE rooms
                    SET updated_at = now()
                    WHERE id = {room_id}
                """)

        notify(db, user_id)
        return user_id


def disconnect(sids: list[str]) -> None:
    with lobby_transaction() as db:
        # An old socket must never erase the connection that replaced it.
        db.execute(t"""
            UPDATE rooms
            SET updated_at = now()
            WHERE id IN (
                SELECT room_id FROM players WHERE sid = ANY({sids})
            )
        """)

        players = db.execute(t"""
            UPDATE players
            SET sid        = NULL,
                token_hash = NULL,
                waiting_at = NULL
            WHERE sid = ANY({sids})
            RETURNING user_id
        """).fetchall()

        notify(db, *(player["user_id"] for player in players))


def heartbeat(sids: list[str]) -> None:
    with lobby_transaction() as db:
        # Check deadlines before a late reconnect or heartbeat can renew them.
        expire(db)

        db.execute(t"""
            UPDATE players
            SET seen_at = now()
            WHERE sid = ANY({sids})
        """)


def create(db, white_id: int, black_id: int | None = None) -> str:
    count = db.execute("""
        SELECT count(*) AS total
        FROM rooms
        WHERE NOT finished
    """).fetchone()

    assert count is not None
    if count["total"] >= MAX_ROOMS:
        raise HTTPException(503, "The server is full. Please try again later.")

    while True:
        room_id = secrets.token_hex(6)
        record  = Jsonb(Game().record())

        inserted = db.execute(t"""
            INSERT INTO rooms (id, white_id, black_id, game)
            VALUES ({room_id}, {white_id}, {black_id}, {record})
            ON CONFLICT (id) DO NOTHING
            RETURNING id
        """).fetchone()

        if inserted:
            return room_id


def enter(sid: str, entry: Entry) -> None:
    with lobby_transaction() as db:
        expire(db)

        player  = authenticated(db, sid)
        user_id = player["user_id"]
        room_id = player["room_id"]

        current_room = db.execute(t"""
            SELECT *
            FROM rooms
            WHERE id = {room_id}
            FOR UPDATE
        """).fetchone()

        if entry.type == "cancel":
            if (
                current_room
                and current_room["black_id"] is not None
                and not current_room["finished"]
            ):
                raise InvalidAction("Resign before leaving an ongoing game.")

            db.execute(t"""
                UPDATE players
                SET room_id    = NULL,
                    waiting_at = NULL
                WHERE user_id = {user_id}
            """)

            if current_room and current_room["black_id"] is None:
                db.execute(t"DELETE FROM rooms WHERE id = {room_id}")

            notify(db, user_id)
            return

        if current_room is not None and not current_room["finished"]:
            raise InvalidAction("Leave your current game before starting another.")

        participants = [user_id]

        if entry.type == "match":
            opponent = db.execute(t"""
                SELECT user_id
                FROM online_players
                WHERE waiting_at IS NOT NULL
                  AND room_id IS NULL
                  AND user_id <> {user_id}
                ORDER BY waiting_at, user_id
                LIMIT 1
            """).fetchone()

            if opponent is None:
                db.execute(t"""
                    UPDATE players
                    SET room_id    = NULL,
                        waiting_at = COALESCE(waiting_at, now())
                    WHERE user_id = {user_id}
                """)

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

            room_id = entry.id

            invited_room = db.execute(t"""
                SELECT *
                FROM rooms
                WHERE id = {room_id}
                FOR UPDATE
            """).fetchone()

            if invited_room is None:
                raise HTTPException(404, "This game does not exist or has expired.")
            if invited_room["black_id"] is not None:
                raise HTTPException(409, "This game already has two players.")

            db.execute(t"""
                UPDATE rooms
                SET black_id   = {user_id},
                    updated_at = now()
                WHERE id = {room_id}
            """)

        else:
            raise InvalidAction("Unknown room action.")

        db.execute(t"""
            UPDATE players
            SET room_id    = {room_id},
                waiting_at = NULL
            WHERE user_id = ANY({participants})
        """)

        notify(db, user_id)


def command(sid: str, command: Command) -> None:
    with pool.connection() as db:
        room = db.execute(t"""
            SELECT room.*
            FROM rooms AS room
            JOIN players AS player ON player.room_id = room.id
            WHERE player.sid = {sid}
            FOR UPDATE OF room
        """).fetchone()

        # A connection or seat may have changed while we waited for the room.
        player = authenticated(db, sid)
        if room is None or room["id"] != player["room_id"]:
            raise InvalidAction("Join a game first.")

        room_id = room["id"]
        game    = Game.restore(room["game"])
        color   = "white" if player["user_id"] == room["white_id"] else "black"

        if command.revision != game.revision:
            raise InvalidAction("The game has changed. Please try again.")
        if room["black_id"] is None:
            raise InvalidAction("Please wait for your opponent to join.")

        connected = db.execute(t"""
            SELECT count(*) AS total
            FROM online_players
            WHERE room_id = {room_id}
        """).fetchone()

        assert connected is not None
        if command.type != "resign" and connected["total"] < 2:
            raise InvalidAction("Please wait for your opponent to reconnect.")

        game.apply(color, command.type, command.move)
        record   = Jsonb(game.record())
        finished = game.outcome is not None

        db.execute(t"""
            UPDATE rooms
            SET game       = {record},
                finished   = {finished},
                updated_at = now()
            WHERE id = {room_id}
        """)

        notify(db, player["user_id"])


def read(sids: list[str]) -> dict[str, dict]:
    # A single statement sees seating, game and presence from the same DB snapshot.
    with pool.connection() as db:
        rows = db.execute(t"""
            SELECT
                player.sid, player.user_id, player.waiting_at,
                room.id AS room_id, room.game,

                room.white_id,
                white_user.username AS white_name,
                white_user.avatar_version AS white_avatar,
                white_presence.user_id IS NOT NULL AS white_online,

                room.black_id,
                black_user.username AS black_name,
                black_user.avatar_version AS black_avatar,
                black_presence.user_id IS NOT NULL AS black_online

            FROM players AS player
            JOIN sessions AS session
                ON session.token_hash = player.token_hash
               AND session.user_id = player.user_id

            LEFT JOIN rooms AS room ON room.id = player.room_id
            LEFT JOIN users AS white_user ON white_user.id = room.white_id
            LEFT JOIN users AS black_user ON black_user.id = room.black_id

            LEFT JOIN online_players AS white_presence
                ON white_presence.user_id = white_user.id
               AND white_presence.room_id = room.id
            LEFT JOIN online_players AS black_presence
                ON black_presence.user_id = black_user.id
               AND black_presence.room_id = room.id

            WHERE player.sid = ANY({sids})
              AND session.expires_at > now()
        """).fetchall()

    return {row["sid"]: row for row in rows}


def snapshot(row: dict) -> dict:
    seat = None
    game = None

    if row["room_id"] is not None:
        color = "white" if row["user_id"] == row["white_id"] else "black"
        seat  = {"id": row["room_id"], "color": color}
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
