from dataclasses import dataclass, field, asdict 
from typing      import Literal 

Color = Literal["white", "black"]

@dataclass 
class Chat:
    sender  : Color | None = None
    content : str          = ""


@dataclass 
class ChatRoom: 

    logs : list[Chat] = field(default_factory=list)


    def recent_logs(self, n: int) -> list[dict[str, str | None]]:
        if n < 0:
            raise ValueError("n must be non-negative")

        return [
            asdict(chat)
            for chat in self.logs[-1 : -n - 1 : -1]
        ]

    def append(self, chat: Chat): 
        self.logs.append(chat)


    