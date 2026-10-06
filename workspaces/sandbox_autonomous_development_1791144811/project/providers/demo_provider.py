from .base import LLMProvider


class DemoProvider(LLMProvider):

    @property
    def name(self) -> str:
        return "demo"

    @property
    def available(self) -> bool:
        return True

    def generate(self, prompt: str) -> str:
        return f"Demo response from IRIS. You asked: {prompt}"
