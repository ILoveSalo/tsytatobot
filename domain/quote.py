from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from domain.phrase import Phrase


@dataclass
class Quote:
    phrases: list[Phrase] = field(default_factory=list)
    date: datetime = field(default_factory=datetime.today)
    main_speaker_name: Optional[str] = None
