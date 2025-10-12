import json
import os
from domain.speaker import Speaker
from persistence.speaker_repository import SpeakerRepository

class JsonSpeakerRepository(SpeakerRepository):
    def __init__(self, file_path: str):
        self.file_path = file_path
        if os.path.exists(self.file_path):
            with open(self.file_path, "r", encoding="utf-8") as f:
                try:
                    data = json.load(f)
                    self.speakers = [
                        Speaker(item["name"], item.get("speaker_image_id"))
                        for item in data
                    ]
                except json.JSONDecodeError:
                    self.speakers = []
        else:
            self.speakers = []

    def get_speakers(self):
        """Return all stored Speaker objects."""
        return self.speakers

    def save_speaker(self, speaker: Speaker):
        """Add a speaker if not already present, and save to JSON file."""
        existing = next((s for s in self.speakers if s.name == speaker.name), None)

        if existing:
            # update image if it’s new
            if speaker.speaker_image_id and existing.speaker_image_id != speaker.speaker_image_id:
                existing.speaker_image_id = speaker.speaker_image_id
        else:
            self.speakers.append(speaker)

        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(
                [
                    {"name": s.name, "speaker_image_id": s.speaker_image_id}
                    for s in self.speakers
                ],
                f,
                ensure_ascii=False,
                indent=2
            )

    def get_speaker(self, name: str) -> Speaker | None:
        """Return the Speaker object with the given name, or None if not found."""
        return next((s for s in self.speakers if s.name == name), None)
