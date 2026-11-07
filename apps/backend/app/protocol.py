"""HTTP and Socket.IO messages at the application boundary."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from .game import Action, BoardSnapshot, Color


RoomId = Annotated[str, Field(pattern = r"^[a-f0-9]{12}$")]


class Health(BaseModel):
    status  : Literal["ok"]


class Auth(BaseModel):
    model_config = ConfigDict(extra = "forbid", strict = True)

    token : str = Field(min_length = 43, max_length = 43)
    id    : RoomId | None = None


class Profile(BaseModel):
    id       : int
    username : str
    avatar   : str | None

    @classmethod
    def for_user(cls, user_id: int, username: str, avatar_version: str) -> "Profile":
        avatar = None
        if avatar_version:
            avatar = f"/api/users/{user_id}/avatar?v={avatar_version}"

        return cls(id = user_id, username = username, avatar = avatar)


class SessionResponse(BaseModel):
    user  : Profile
    token : str


class Credentials(BaseModel):
    username : str = Field(pattern = r"^[a-zA-Z0-9_]{3,24}$")
    password : str = Field(min_length = 8, max_length = 128)


class Entry(BaseModel):
    model_config = ConfigDict(extra = "forbid", strict = True)

    type : Literal["create", "join", "match", "cancel"]
    id   : RoomId | None = None


class Command(BaseModel):
    model_config = ConfigDict(extra = "forbid", strict = True)

    type     : Action
    revision : int = Field(ge = 0)
    move     : str = Field(default = "", max_length = 5)


class Seat(BaseModel):
    id    : str
    color : Color


class GameSnapshot(BoardSnapshot):
    players   : dict[Color, Profile]
    connected : list[Color]


class RoomSnapshot(BaseModel):
    seat    : Seat | None
    waiting : bool
    game    : GameSnapshot | None
