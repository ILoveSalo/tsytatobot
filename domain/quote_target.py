from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class QuoteTarget:
    chat_id: int | str
    title: str
    type: str
    allow_viewers: bool = False
    registered_by_user_id: Optional[int] = None
    registered_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    @property
    def key(self) -> str:
        return str(self.chat_id)

    @property
    def is_channel(self) -> bool:
        return self.type == "channel"

    @property
    def is_group(self) -> bool:
        return self.type in ("group", "supergroup")
