from abc import ABC, abstractmethod

from domain.quote_target import QuoteTarget


class TargetRepository(ABC):
    @abstractmethod
    def get_targets(self) -> list[QuoteTarget]:
        raise NotImplementedError

    @abstractmethod
    def get_target(self, chat_id: int | str) -> QuoteTarget | None:
        raise NotImplementedError

    @abstractmethod
    def save_target(self, target: QuoteTarget) -> None:
        raise NotImplementedError

    @abstractmethod
    def set_allow_viewers(self, chat_id: int | str, allow_viewers: bool) -> QuoteTarget | None:
        raise NotImplementedError
