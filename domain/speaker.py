from dataclasses import dataclass
from typing import Optional


@dataclass
class Speaker:
    name: str
    speaker_image_id: Optional[str] = None
