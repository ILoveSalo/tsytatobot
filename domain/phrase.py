from dataclasses import dataclass
from typing import Optional

from domain.speaker import Speaker


@dataclass
class Phrase:
    speaker: Optional[Speaker]
    text: str
    context_text: Optional[str] = None
    speaker_image_id: Optional[str] = None
