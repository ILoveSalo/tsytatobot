import json
import os
import tempfile
import threading
from collections import defaultdict

from domain.speaker import Speaker
from persistence.speaker_repository import SpeakerRepository


GLOBAL_CHAT_KEY = "__global__"


def normalize_speaker_key(name: str) -> str:
    return " ".join(name.split()).casefold()


class JsonSpeakerRepository(SpeakerRepository):
    def __init__(self, file_path: str):
        self.file_path = file_path
        self._lock = threading.Lock()
        self._speakers_by_chat: dict[str, list[Speaker]] = defaultdict(list)
        self._index_by_chat: dict[str, dict[str, Speaker]] = defaultdict(dict)
        self._load()

    def _chat_key(self, chat_id: int | None) -> str:
        return str(chat_id) if chat_id is not None else GLOBAL_CHAT_KEY

    def _load(self) -> None:
        if not os.path.exists(self.file_path):
            return

        with open(self.file_path, "r", encoding="utf-8") as file:
            try:
                data = json.load(file)
            except json.JSONDecodeError:
                return

        # Backward compatibility with old format: [ {name, speaker_image_id}, ... ]
        if isinstance(data, list):
            data = {GLOBAL_CHAT_KEY: data}

        if not isinstance(data, dict):
            return

        for chat_key, speakers in data.items():
            if not isinstance(speakers, list):
                continue
            for item in speakers:
                name = item.get("name") if isinstance(item, dict) else None
                if not name:
                    continue
                speaker = Speaker(name=name, speaker_image_id=item.get("speaker_image_id"))
                self._speakers_by_chat[chat_key].append(speaker)
                self._index_by_chat[chat_key][normalize_speaker_key(name)] = speaker

    def _serialize(self) -> dict[str, list[dict[str, str | None]]]:
        serialized: dict[str, list[dict[str, str | None]]] = {}
        for chat_key, speakers in self._speakers_by_chat.items():
            serialized[chat_key] = [
                {"name": speaker.name, "speaker_image_id": speaker.speaker_image_id}
                for speaker in speakers
            ]
        return serialized

    def _write_atomic(self) -> None:
        directory = os.path.dirname(self.file_path) or "."
        os.makedirs(directory, exist_ok=True)
        payload = self._serialize()

        fd, temp_path = tempfile.mkstemp(prefix="speakers_", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as temp_file:
                json.dump(payload, temp_file, ensure_ascii=False, indent=2)
            os.replace(temp_path, self.file_path)
        except Exception:
            try:
                os.remove(temp_path)
            except OSError:
                pass
            raise

    def get_speakers(self, chat_id: int | None = None) -> list[Speaker]:
        chat_key = self._chat_key(chat_id)
        with self._lock:
            return list(self._speakers_by_chat.get(chat_key, []))

    def save_speaker(self, speaker: Speaker, chat_id: int | None = None) -> None:
        chat_key = self._chat_key(chat_id)
        key = normalize_speaker_key(speaker.name)

        with self._lock:
            existing = self._index_by_chat[chat_key].get(key)
            if existing:
                # Preserve latest display name and refresh image when provided.
                existing.name = speaker.name
                if speaker.speaker_image_id and existing.speaker_image_id != speaker.speaker_image_id:
                    existing.speaker_image_id = speaker.speaker_image_id
            else:
                self._speakers_by_chat[chat_key].append(speaker)
                self._index_by_chat[chat_key][key] = speaker
            self._write_atomic()

    def get_speaker(self, name: str, chat_id: int | None = None) -> Speaker | None:
        chat_key = self._chat_key(chat_id)
        key = normalize_speaker_key(name)
        with self._lock:
            return self._index_by_chat.get(chat_key, {}).get(key)
