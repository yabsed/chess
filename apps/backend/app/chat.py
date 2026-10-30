"""Store an append-only chat log and return pages in chronological order."""

from dataclasses import asdict, dataclass, field


@dataclass
class Chat:
    id      : int
    sender  : str
    content : str


@dataclass
class ChatLog:
    logs: list[Chat] = field(default_factory = list, init = False)

    def append(self, sender: str, content: str) -> Chat:
        chat = Chat(id = len(self.logs) + 1, sender = sender, content = content)
        self.logs.append(chat)
        return chat

    def history(self, limit: int, before: int | None = None) -> dict:
        if limit < 0:
            raise ValueError("limit must be non-negative")
        if before is not None and before < 1:
            raise ValueError("before must be a positive message ID")

        # Messages are never removed, so ID n is stored at index n - 1.
        end = len(self.logs)
        if before is not None:
            end = min(end, before - 1)
        start = max(0, end - limit)

        logs = [
            asdict(chat)
            for chat in self.logs[start:end]
        ]

        return {
            "before"   : before,
            "logs"     : logs,
            "has_more" : start > 0,
        }
