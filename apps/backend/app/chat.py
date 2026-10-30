"""Store chat messages and return recent history, newest first."""

from dataclasses import asdict, dataclass, field


@dataclass
class Chat:
    sender  : str
    content : str


@dataclass
class ChatLog:
    logs: list[Chat] = field(default_factory = list)

    def append(self, chat: Chat) -> None:
        self.logs.append(chat)

    def recent(self, limit: int) -> list[dict[str, str]]:
        if limit < 0:
            raise ValueError("limit must be non-negative")
        if limit == 0:
            return []

        return [
            asdict(chat) 
            for chat in reversed(self.logs[-limit:])
        ]
