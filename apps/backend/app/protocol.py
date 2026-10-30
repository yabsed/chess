"""HTTP responses and Socket.IO inputs at the application boundary."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from .game import Action, Color


RoomId = Annotated[str, Field(pattern = r"^[a-f0-9]{12}$")]


class Health(BaseModel):
    status: Literal["ok"]


class Session(BaseModel):
    """A reserved seat. Keep its token to reconnect after a refresh."""

    id    : RoomId
    token : str
    color : Color


class ApiError(BaseModel):
    detail: str


class Auth(BaseModel):
    model_config = ConfigDict(extra = "forbid", strict = True)

    id    : RoomId
    token : str = Field(min_length = 1, max_length = 100)


class Command(BaseModel):
    model_config = ConfigDict(extra = "forbid", strict = True)

    type     : Action
    revision : int = Field(ge = 0)
    move     : str = Field(default = "", max_length = 5)
