import json
import os
import tempfile
import threading
from datetime import datetime

from domain.quote_target import QuoteTarget
from persistence.target_repository import TargetRepository


class JsonTargetRepository(TargetRepository):
    def __init__(self, file_path: str):
        self.file_path = file_path
        self._lock = threading.Lock()
        self._targets_by_key: dict[str, QuoteTarget] = {}
        self._load()

    def _target_key(self, chat_id: int | str) -> str:
        return str(chat_id)

    def _load(self) -> None:
        if not os.path.exists(self.file_path):
            return

        with open(self.file_path, "r", encoding="utf-8") as file:
            try:
                data = json.load(file)
            except json.JSONDecodeError:
                return

        if not isinstance(data, list):
            return

        for item in data:
            if not isinstance(item, dict):
                continue

            chat_id = item.get("chat_id")
            title = item.get("title")
            chat_type = item.get("type")
            if chat_id is None or not title or not chat_type:
                continue

            target = QuoteTarget(
                chat_id=chat_id,
                title=title,
                type=chat_type,
                allow_viewers=bool(item.get("allow_viewers", False)),
                registered_by_user_id=item.get("registered_by_user_id"),
                registered_at=item.get("registered_at") or datetime.now().isoformat(timespec="seconds"),
            )
            self._targets_by_key[target.key] = target

    def _serialize(self) -> list[dict[str, int | str | bool | None]]:
        return [
            {
                "chat_id": target.chat_id,
                "title": target.title,
                "type": target.type,
                "allow_viewers": target.allow_viewers,
                "registered_by_user_id": target.registered_by_user_id,
                "registered_at": target.registered_at,
            }
            for target in self._targets_by_key.values()
        ]

    def _write_atomic(self) -> None:
        directory = os.path.dirname(self.file_path) or "."
        os.makedirs(directory, exist_ok=True)

        fd, temp_path = tempfile.mkstemp(prefix="targets_", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as temp_file:
                json.dump(self._serialize(), temp_file, ensure_ascii=False, indent=2)
            os.replace(temp_path, self.file_path)
        except Exception:
            try:
                os.remove(temp_path)
            except OSError:
                pass
            raise

    def get_targets(self) -> list[QuoteTarget]:
        with self._lock:
            return list(self._targets_by_key.values())

    def get_target(self, chat_id: int | str) -> QuoteTarget | None:
        with self._lock:
            return self._targets_by_key.get(self._target_key(chat_id))

    def save_target(self, target: QuoteTarget) -> None:
        with self._lock:
            self._targets_by_key[target.key] = target
            self._write_atomic()

    def set_allow_viewers(self, chat_id: int | str, allow_viewers: bool) -> QuoteTarget | None:
        with self._lock:
            target = self._targets_by_key.get(self._target_key(chat_id))
            if target is None:
                return None

            target.allow_viewers = allow_viewers
            self._write_atomic()
            return target
