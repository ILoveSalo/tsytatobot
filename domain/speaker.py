from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Speaker:
    name: str
    speaker_image_ids: list[str] = field(default_factory=list)

    @property
    def speaker_image_id(self) -> Optional[str]:
        return self.speaker_image_ids[0] if self.speaker_image_ids else None

    @speaker_image_id.setter
    def speaker_image_id(self, value: Optional[str]) -> None:
        if value:
            if self.speaker_image_ids and self.speaker_image_ids[0] == value:
                return
            self.speaker_image_ids = [value] + [image_id for image_id in self.speaker_image_ids if image_id != value]
        else:
            self.speaker_image_ids = []

    def add_image_id(self, image_id: str) -> None:
        if image_id not in self.speaker_image_ids:
            self.speaker_image_ids.append(image_id)
