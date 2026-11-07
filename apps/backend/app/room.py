"""Seat players and commit moves. No room or player belongs to a server."""

import secrets

from dataclasses import dataclass
from datetime    import timedelta

from fastapi        import HTTPException
from sqlalchemy     import Text, cast, delete, func, or_, select, update
from sqlalchemy.orm import Session, aliased

from .db       import lobby_transaction, sessions
from .game     import Color, Game, GameRecord, InvalidAction
from .models   import LoginSession, Player, Room, User as Account
from .protocol import Command, Entry, GameSnapshot, RoomSnapshot, Seat, User


MAX_ROOMS = 500

online = (
    select(Player)
    .join(LoginSession, LoginSession.token_hash == Player.token_hash)
    .where(
        LoginSession.user_id == Player.user_id,
        LoginSession.expires_at > func.now(),
        Player.sid.is_not(None),
        Player.seen_at > func.now() - timedelta(seconds = 35),
    )
    .cte("online_players")
)

OnlinePlayer = aliased(Player, online)


@dataclass(frozen = True)
class RoomState:
    user_id   : int
    waiting   : bool
    room_id   : str | None
    record    : GameRecord | None
    white     : User | None
    black     : User | None
    connected : tuple[Color, ...]


def notify(db: Session, *user_ids: int) -> None:
    # Write pending changes before finding everyone who needs an update.
    db.flush()

    statement = (
        select(func.pg_notify("chess", cast(Player.user_id, Text)))
        .outerjoin(Room, Room.id == Player.room_id)
        .where(or_(
            Player.user_id.in_(user_ids),
            Room.white_id.in_(user_ids),
            Room.black_id.in_(user_ids),
        ))
    )
    db.execute(statement)


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
    White = aliased(Player, name = "white")
    Black = aliased(Player, name = "black")
    deadline = func.now() - timedelta(seconds = 60)

    statement = (
        select(Room, White.user_id.is_not(None), Black.user_id.is_not(None))
        .outerjoin(White, (
            (White.user_id == Room.white_id)
            & (White.room_id == Room.id)
            & (White.seen_at.is_(None) | (White.seen_at <= deadline))
        ))
        .outerjoin(Black, (
            (Black.user_id == Room.black_id)
            & (Black.room_id == Room.id)
            & (Black.seen_at.is_(None) | (Black.seen_at <= deadline))
        ))
        .where(
            Room.finished.is_(False),
            or_(White.user_id.is_not(None), Black.user_id.is_not(None)),
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

            game = Game.restore(GameRecord.model_validate(room.game))
            game.abandon(loser)

            room.game       = game.record().model_dump()
            room.finished   = True
            room.updated_at = func.now()

        notify(db, room.white_id)

    recent_players = (
        select(Player.user_id)
        .where(
            Player.room_id == Room.id,
            Player.seen_at > func.now() - timedelta(hours = 24),
        )
        .exists()
    )
    db.execute(
        delete(Room).where(
            Room.updated_at < func.now() - timedelta(hours = 24),
            ~recent_players,
        )
    )


def connect(sid: str, key: str, invited_room_id: str | None) -> int:
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

        notify(db, user_id)
        return user_id


def disconnect(sids: list[str]) -> None:
    with lobby_transaction() as db:
        # An old socket must never erase the connection that replaced it.
        occupied = select(Player.room_id).where(Player.sid.in_(sids))
        db.execute(
            update(Room)
            .where(Room.id.in_(occupied))
            .values(updated_at = func.now())
        )

        users = db.scalars(
            update(Player)
            .where(Player.sid.in_(sids))
            .values(sid = None, token_hash = None, waiting_at = None)
            .returning(Player.user_id)
        ).all()

        notify(db, *users)


def heartbeat(sids: list[str]) -> None:
    with lobby_transaction() as db:
        expire(db)
        db.execute(
            update(Player)
            .where(Player.sid.in_(sids))
            .values(seen_at = func.now())
        )


def create(db: Session, white_id: int, black_id: int | None = None) -> Room:
    count = db.execute(
        select(func.count()).select_from(Room).where(Room.finished.is_(False))
    ).scalar_one()
    if count >= MAX_ROOMS:
        raise HTTPException(503, "The server is full. Please try again later.")

    # All room creation holds the seating lock.
    room_id = secrets.token_hex(6)
    while db.get(Room, room_id) is not None:
        room_id = secrets.token_hex(6)

    room = Room(
        id       = room_id,
        white_id = white_id,
        black_id = black_id,
        game     = Game().record().model_dump(),
    )
    db.add(room)
    db.flush()  # The room must exist before assigning its players.
    return room


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

            notify(db, player.user_id)
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
                notify(db, player.user_id)
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

        notify(db, player.user_id)


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

        game  = Game.restore(GameRecord.model_validate(room.game))
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

        room.game       = game.record().model_dump()
        room.finished   = game.outcome is not None
        room.updated_at = func.now()

        notify(db, player.user_id)


def read(sids: list[str]) -> dict[str, RoomState]:
    White = aliased(Account, name = "white_user")
    Black = aliased(Account, name = "black_user")
    WhitePresence = aliased(Player, online.alias("white_presence"))
    BlackPresence = aliased(Player, online.alias("black_presence"))

    # One query sees seats, games, profiles and presence in the same snapshot.
    statement = (
        select(
            Player, Room, White, Black,
            WhitePresence.user_id.is_not(None),
            BlackPresence.user_id.is_not(None),
        )
        .join(LoginSession, LoginSession.token_hash == Player.token_hash)
        .outerjoin(Room, Room.id == Player.room_id)
        .outerjoin(White, White.id == Room.white_id)
        .outerjoin(Black, Black.id == Room.black_id)
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
                white_profile = User.profile(white.id, white.username, white.avatar_version)
            if black:
                black_profile = User.profile(black.id, black.username, black.avatar_version)

            states[player.sid] = RoomState(
                user_id   = player.user_id,
                waiting   = player.waiting_at is not None,
                room_id   = room.id if room else None,
                record    = GameRecord.model_validate(room.game) if room else None,
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
    board = Game.restore(state.record).snapshot()

    players: dict[Color, User] = {"white": state.white}
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
