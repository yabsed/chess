"""HTTP responses and Socket.IO inputs at the application boundary."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from .game import Action, Color


RoomId = Annotated[str, Field(pattern = r"^[a-f0-9]{12}$")]


class Health(BaseModel):
    status: Literal["ok"]


class Seat(BaseModel):
    """A seat owned by the signed-in user."""

    id    : RoomId
    color : Color


class Auth(BaseModel):
    model_config = ConfigDict(extra = "forbid", strict = True)

    token : str = Field(min_length = 43, max_length = 43)
    id    : RoomId | None = None


class User(BaseModel):
    id       : int
    username : str
    avatar   : str | None


class Session(BaseModel):
    user  : User
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
