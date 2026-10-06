from .base import LLMProvider
from config.settings import OPENAI_API_KEY


class OpenAIProvider(LLMProvider):

    @property
    def name(self) -> str:
        return "openai"

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def __init__(self):
        self.api_key = OPENAI_API_KEY

    def generate(self, prompt: str) -> str:

        if not self.api_key:
            return "OpenAI is not configured yet."

        from openai import OpenAI

        client = OpenAI(api_key=self.api_key)

        response = client.responses.create(
            model="gpt-5.6-luna",
            input=prompt
        )

        return response.output_text
