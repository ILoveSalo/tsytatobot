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

    def _chat_key(self, chat_id: int | str | None) -> str:
        return str(chat_id) if chat_id is not None else GLOBAL_CHAT_KEY

    def _unique_image_ids(self, image_ids: list[str]) -> list[str]:
        unique: list[str] = []
        seen: set[str] = set()
        for image_id in image_ids:
            if image_id in seen:
                continue
            seen.add(image_id)
            unique.append(image_id)
        return unique

    def _reindex_chat(self, chat_key: str) -> None:
        self._index_by_chat[chat_key] = {
            normalize_speaker_key(speaker.name): speaker
            for speaker in self._speakers_by_chat[chat_key]
        }

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

                image_ids: list[str] = []
                if isinstance(item, dict):
                    if isinstance(item.get("speaker_image_ids"), list):
                        image_ids = [image_id for image_id in item["speaker_image_ids"] if isinstance(image_id, str)]
                    elif item.get("speaker_image_id"):
                        image_ids = [item["speaker_image_id"]]

                speaker = Speaker(name=name, speaker_image_ids=self._unique_image_ids(image_ids))
                self._speakers_by_chat[chat_key].append(speaker)

            self._reindex_chat(chat_key)

    def _serialize(self) -> dict[str, list[dict[str, str | list[str]]]]:
        serialized: dict[str, list[dict[str, str | list[str]]]] = {}
        for chat_key, speakers in self._speakers_by_chat.items():
            serialized[chat_key] = [
                {
                    "name": speaker.name,
                    "speaker_image_ids": self._unique_image_ids(speaker.speaker_image_ids),
                }
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

    def get_speakers(self, chat_id: int | str | None = None) -> list[Speaker]:
        chat_key = self._chat_key(chat_id)
        with self._lock:
            return list(self._speakers_by_chat.get(chat_key, []))

    def save_speaker(self, speaker: Speaker, chat_id: int | str | None = None) -> None:
        chat_key = self._chat_key(chat_id)
        key = normalize_speaker_key(speaker.name)

        with self._lock:
            existing = self._index_by_chat[chat_key].get(key)
            cleaned_image_ids = self._unique_image_ids(list(speaker.speaker_image_ids))

            if existing:
                existing.name = speaker.name
                existing.speaker_image_ids = cleaned_image_ids
            else:
                new_speaker = Speaker(name=speaker.name, speaker_image_ids=cleaned_image_ids)
                self._speakers_by_chat[chat_key].append(new_speaker)

            self._reindex_chat(chat_key)
            self._write_atomic()

    def get_speaker(self, name: str, chat_id: int | str | None = None) -> Speaker | None:
        chat_key = self._chat_key(chat_id)
        key = normalize_speaker_key(name)
        with self._lock:
            return self._index_by_chat.get(chat_key, {}).get(key)

    def rename_speaker(self, old_name: str, new_name: str, chat_id: int | str | None = None) -> Speaker | None:
        chat_key = self._chat_key(chat_id)
        old_key = normalize_speaker_key(old_name)
        new_key = normalize_speaker_key(new_name)

        with self._lock:
            speakers_by_name = self._index_by_chat.get(chat_key, {})
            source = speakers_by_name.get(old_key)
            if source is None:
                return None

            if old_key == new_key:
                source.name = new_name
                self._reindex_chat(chat_key)
                self._write_atomic()
                return source

            target = speakers_by_name.get(new_key)
            if target and target is not source:
                merged_ids = self._unique_image_ids(target.speaker_image_ids + source.speaker_image_ids)
                target.speaker_image_ids = merged_ids
                source_list = self._speakers_by_chat[chat_key]
                self._speakers_by_chat[chat_key] = [speaker for speaker in source_list if speaker is not source]
                self._reindex_chat(chat_key)
                self._write_atomic()
                return target

            source.name = new_name
            self._reindex_chat(chat_key)
            self._write_atomic()
            return source
