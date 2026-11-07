"""Seat players and commit moves. No room or player belongs to a server."""

import secrets

from dataclasses import dataclass
from datetime    import timedelta

from fastapi        import HTTPException
from sqlalchemy     import delete, func, or_, select, update
from sqlalchemy.orm import Session, aliased

from .db       import lobby_transaction, notify, sessions
from .game     import Color, Game, GameRecord, InvalidAction
from .models   import LoginSession, Player, Room, User
from .protocol import Command, Entry, GameSnapshot, Profile, RoomSnapshot, Seat


MAX_ROOMS = 500

# Both deadlines use Player.seen_at: first offline, then abandonment.
HEARTBEAT_SECONDS = 10
OFFLINE_SECONDS   = 35
ABANDON_SECONDS   = 60

ROOM_RETENTION = timedelta(hours = 24)


@dataclass(frozen = True)
class RoomState:
    user_id   : int
    waiting   : bool
    room_id   : str | None
    record    : GameRecord | None
    white     : Profile | None
    black     : Profile | None
    connected : tuple[Color, ...]


# Connections


def connect(sid: str, key: str, invited_room_id: str | None) -> None:
    with lobby_transaction() as db:
        login = db.scalar(
            select(LoginSession).where(
                LoginSession.token_hash == key,
                LoginSession.expires_at > func.now(),
            )
        )
        if login is None:
            raise HTTPException(401, "Please sign in again.")

        # A late reconnect must not erase an expired deadline.
        expire(db)
        user_id = login.user_id

        player = db.get(Player, user_id)
        if player is None:
            player = Player(user_id = user_id)
            db.add(player)

        player.sid        = sid
        player.token_hash = key
        player.seen_at    = func.now()
        player.waiting_at = None

        current_room = db.scalar(
            select(Room)
            .where(Room.id == player.room_id)
            .with_for_update()
        )

        if current_room is not None and not current_room.finished:
            current_room.updated_at = func.now()

        else:
            previous_room = db.scalar(
                select(Room).where(
                    Room.id == invited_room_id,
                    or_(Room.white_id == user_id, Room.black_id == user_id),
                )
            )
            player.room_id = previous_room.id if previous_room else None

            if previous_room:
                previous_room.updated_at = func.now()

        notify(db)


def disconnect(sids: list[str]) -> None:
    with lobby_transaction() as db:
        # An old socket must never erase the connection that replaced it.
        occupied = select(Player.room_id).where(Player.sid.in_(sids))
        db.execute(
            update(Room)
            .where(Room.id.in_(occupied))
            .values(updated_at = func.now())
        )

        db.execute(
            update(Player)
            .where(Player.sid.in_(sids))
            .values(sid = None, token_hash = None, waiting_at = None)
        )

        notify(db)


def heartbeat(sids: list[str]) -> None:
    with lobby_transaction() as db:
        expire(db)
        db.execute(
            update(Player)
            .where(Player.sid.in_(sids))
            .values(seen_at = func.now())
        )


# Seating and moves


def enter(sid: str, entry: Entry) -> None:
    with lobby_transaction() as db:
        expire(db)
        player = authenticated(db, sid)

        current_room = db.scalar(
            select(Room)
            .where(Room.id == player.room_id)
            .with_for_update()
        )

        if entry.type == "cancel":
            if (
                current_room
                and current_room.black_id is not None
                and not current_room.finished
            ):
                raise InvalidAction("Resign before leaving an ongoing game.")

            player.room_id    = None
            player.waiting_at = None

            if current_room and current_room.black_id is None:
                db.delete(current_room)

            notify(db)
            return

        if current_room is not None and not current_room.finished:
            raise InvalidAction("Leave your current game before starting another.")

        participants = [player]

        if entry.type == "match":
            opponent = db.scalar(
                select(OnlinePlayer)
                .where(
                    OnlinePlayer.waiting_at.is_not(None),
                    OnlinePlayer.room_id.is_(None),
                    OnlinePlayer.user_id != player.user_id,
                )
                .order_by(OnlinePlayer.waiting_at, OnlinePlayer.user_id)
                .limit(1)
            )

            if opponent is None:
                player.room_id    = None
                player.waiting_at = player.waiting_at or func.now()
                notify(db)
                return

            participants.append(opponent)
            if secrets.randbelow(2):
                participants.reverse()
            room = create(db, participants[0].user_id, participants[1].user_id)

        elif entry.type == "create":
            room = create(db, player.user_id)

        elif entry.type == "join":
            if entry.id is None:
                raise InvalidAction("Choose a game to join.")

            room = db.scalar(
                select(Room)
                .where(Room.id == entry.id)
                .with_for_update()
            )
            if room is None:
                raise HTTPException(404, "This game does not exist or has expired.")
            if room.black_id is not None:
                raise HTTPException(409, "This game already has two players.")

            room.black_id   = player.user_id
            room.updated_at = func.now()

        else:
            raise InvalidAction("Unknown room action.")

        for participant in participants:
            participant.room_id    = room.id
            participant.waiting_at = None

        notify(db)


def command(sid: str, command: Command) -> None:
    with sessions.begin() as db:
        room = db.scalar(
            select(Room)
            .join(Player, Player.room_id == Room.id)
            .where(Player.sid == sid)
            .with_for_update(of = Room)
        )

        # A connection or seat may have changed while we waited for the room.
        player = authenticated(db, sid)
        if room is None or room.id != player.room_id:
            raise InvalidAction("Join a game first.")

        record = GameRecord.model_validate(room.game)
        game   = Game.restore(record)

        color = "white" if player.user_id == room.white_id else "black"

        if command.revision != game.revision:
            raise InvalidAction("The game has changed. Please try again.")
        if room.black_id is None:
            raise InvalidAction("Please wait for your opponent to join.")

        connected = db.execute(
            select(func.count())
            .select_from(OnlinePlayer)
            .where(OnlinePlayer.room_id == room.id)
        ).scalar_one()
        if command.type != "resign" and connected < 2:
            raise InvalidAction("Please wait for your opponent to reconnect.")

        game.apply(color, command.type, command.move)
        record = game.record()

        room.game       = record.model_dump()
        room.finished   = game.outcome is not None
        room.updated_at = func.now()

        notify(db)


# State and screens


def read(sids: list[str]) -> dict[str, RoomState]:
    WhiteUser = aliased(User, name = "white_user")
    BlackUser = aliased(User, name = "black_user")
    WhitePresence = aliased(Player, online.alias("white_presence"))
    BlackPresence = aliased(Player, online.alias("black_presence"))

    # One query sees seats, games, profiles and presence in the same snapshot.
    statement = (
        select(
            Player, Room, WhiteUser, BlackUser,
            WhitePresence.user_id.is_not(None),
            BlackPresence.user_id.is_not(None),
        )
        .join(LoginSession, LoginSession.token_hash == Player.token_hash)
        .outerjoin(Room, Room.id == Player.room_id)
        .outerjoin(WhiteUser, WhiteUser.id == Room.white_id)
        .outerjoin(BlackUser, BlackUser.id == Room.black_id)
        .outerjoin(WhitePresence, (
            (WhitePresence.user_id == Room.white_id)
            & (WhitePresence.room_id == Room.id)
        ))
        .outerjoin(BlackPresence, (
            (BlackPresence.user_id == Room.black_id)
            & (BlackPresence.room_id == Room.id)
        ))
        .where(
            Player.sid.in_(sids),
            LoginSession.user_id == Player.user_id,
            LoginSession.expires_at > func.now(),
        )
    )

    states = {}
    with sessions() as db:
        for (
            player, room, white, black,
            white_online, black_online,
        ) in db.execute(statement):
            connected = []
            if white_online:
                connected.append("white")
            if black_online:
                connected.append("black")

            white_profile = None
            black_profile = None
            if white:
                white_profile = Profile.for_user(
                    white.id, white.username, white.avatar_version,
                )
            if black:
                black_profile = Profile.for_user(
                    black.id, black.username, black.avatar_version,
                )

            record = None
            if room:
                record = GameRecord.model_validate(room.game)

            states[player.sid] = RoomState(
                user_id   = player.user_id,
                waiting   = player.waiting_at is not None,
                room_id   = room.id if room else None,
                record    = record,
                white     = white_profile,
                black     = black_profile,
                connected = tuple(connected),
            )

    return states


def snapshot(state: RoomState) -> RoomSnapshot:
    if state.room_id is None:
        return RoomSnapshot(seat = None, waiting = state.waiting, game = None)

    assert state.white is not None and state.record is not None
    color: Color = "white" if state.user_id == state.white.id else "black"
    game  = Game.restore(state.record)
    board = game.snapshot()

    players: dict[Color, Profile] = {"white": state.white}
    if state.black:
        players["black"] = state.black

    return RoomSnapshot(
        seat    = Seat(id = state.room_id, color = color),
        waiting = state.waiting,
        game    = GameSnapshot(
            **board.model_dump(),
            players   = players,
            connected = list(state.connected),
        ),
    )


# Internal helpers

online = (
    select(Player)
    .join(LoginSession, LoginSession.token_hash == Player.token_hash)
    .where(
        LoginSession.user_id == Player.user_id,
        LoginSession.expires_at > func.now(),
        Player.sid.is_not(None),
        Player.seen_at > func.now() - timedelta(seconds = OFFLINE_SECONDS),
    )
    .cte("online_players")
)

OnlinePlayer = aliased(Player, online)


def authenticated(db: Session, sid: str) -> Player:
    statement = (
        select(OnlinePlayer)
        .where(OnlinePlayer.sid == sid)
        .execution_options(populate_existing = True)
    )
    player = db.scalar(statement)

    if player is None:
        raise HTTPException(401, "Your session has ended. Please reconnect.")
    return player


def expire(db: Session) -> None:
    AbsentWhite = aliased(Player, name = "absent_white")
    AbsentBlack = aliased(Player, name = "absent_black")
    abandon_before = func.now() - timedelta(seconds = ABANDON_SECONDS)

    statement = (
        select(
            Room,
            AbsentWhite.user_id.is_not(None),
            AbsentBlack.user_id.is_not(None),
        )
        .outerjoin(AbsentWhite, (
            (AbsentWhite.user_id == Room.white_id)
            & (AbsentWhite.room_id == Room.id)
            & or_(
                AbsentWhite.seen_at.is_(None),
                AbsentWhite.seen_at <= abandon_before,
            )
        ))
        .outerjoin(AbsentBlack, (
            (AbsentBlack.user_id == Room.black_id)
            & (AbsentBlack.room_id == Room.id)
            & or_(
                AbsentBlack.seen_at.is_(None),
                AbsentBlack.seen_at <= abandon_before,
            )
        ))
        .where(
            Room.finished.is_(False),
            or_(
                AbsentWhite.user_id.is_not(None),
                AbsentBlack.user_id.is_not(None),
            ),
        )
        .order_by(Room.id)
        .with_for_update(of = Room)
    )

    for room, white_absent, black_absent in db.execute(statement):
        if room.black_id is None:
            db.delete(room)

        else:
            loser = None
            if not (white_absent and black_absent):
                loser = "white" if white_absent else "black"

            record = GameRecord.model_validate(room.game)
            game   = Game.restore(record)
            game.abandon(loser)

            record = game.record()

            room.game       = record.model_dump()
            room.finished   = True
            room.updated_at = func.now()

        notify(db)

    keep_since = func.now() - ROOM_RETENTION
    has_recent_player = (
        select(Player.user_id)
        .where(
            Player.room_id == Room.id,
            Player.seen_at > keep_since,
        )
        .exists()
    )
    db.execute(
        delete(Room).where(
            Room.updated_at < keep_since,
            ~has_recent_player,
        )
    )


def create(db: Session, white_id: int, black_id: int | None = None) -> Room:
    statement = (
        select(func.count())
        .select_from(Room)
        .where(Room.finished.is_(False))
    )
    count = db.execute(statement).scalar_one()
    if count >= MAX_ROOMS:
        raise HTTPException(503, "The server is full. Please try again later.")

    # All room creation holds the seating lock.
    room_id = secrets.token_hex(6)
    while db.get(Room, room_id) is not None:
        room_id = secrets.token_hex(6)

    game   = Game()
    record = game.record()

    room = Room(
        id       = room_id,
        white_id = white_id,
        black_id = black_id,
        game     = record.model_dump(),
    )
    db.add(room)
    db.flush()  # The room must exist before assigning its players.
    return room
