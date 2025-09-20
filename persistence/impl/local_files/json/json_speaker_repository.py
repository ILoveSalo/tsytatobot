import json
import os
from domain.speaker import Speaker
from persistence.speaker_repository import SpeakerRepository

class JsonSpeakerRepository(SpeakerRepository):
    def __init__(self, file_path: str):
        self.file_path = file_path
        # Load existing speakers, or create empty list if file missing
        if os.path.exists(self.file_path):
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                # convert dicts to Speaker objects
                self.speakers = [Speaker(item["name"]) for item in data]
        else:
            self.speakers = []

    def get_speakers(self):
        """Return all stored Speaker objects."""
        return self.speakers

    def save_speaker(self, speaker: Speaker):
        """Add a speaker if not already present, and save to JSON file."""
        # Check if speaker already exists
        if any(s.name == speaker.name for s in self.speakers):
            return  # already exists, do nothing

        # Add speaker to list
        self.speakers.append(speaker)
        # Save updated list to JSON
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump([{"name": s.name} for s in self.speakers], f, ensure_ascii=False, indent=2)
