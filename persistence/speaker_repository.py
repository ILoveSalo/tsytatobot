from abc import ABC, abstractmethod

from domain.speaker import Speaker


class SpeakerRepository(ABC):
    @abstractmethod
    def get_speakers(self, chat_id: int | str | None = None) -> list[Speaker]:
        raise NotImplementedError

    @abstractmethod
    def save_speaker(self, speaker: Speaker, chat_id: int | str | None = None) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_speaker(self, name: str, chat_id: int | str | None = None) -> Speaker | None:
        raise NotImplementedError

    @abstractmethod
    def rename_speaker(self, old_name: str, new_name: str, chat_id: int | str | None = None) -> Speaker | None:
        raise NotImplementedError
