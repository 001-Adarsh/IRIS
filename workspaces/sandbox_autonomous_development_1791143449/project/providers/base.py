from abc import ABC, abstractmethod


class LLMProvider(ABC):

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @property
    @abstractmethod
    def available(self) -> bool:
        pass

    @abstractmethod
    def generate(self, prompt: str) -> str:
        pass
