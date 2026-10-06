from providers.demo_provider import DemoProvider
from providers.openai_provider import OpenAIProvider
from providers.ollama_provider import OllamaProvider
from providers.gemini_provider import GeminiProvider


class ProviderManager:

    def __init__(self):
        self.providers = {
            "demo": DemoProvider(),
            "ollama": OllamaProvider(),
            "gemini": GeminiProvider(),
            "openai": OpenAIProvider()
        }

    def show_status(self):
        print("\n=== IRIS PROVIDER STATUS ===")

        for name, provider in self.providers.items():
            status = "AVAILABLE" if provider.available else "NOT CONFIGURED"
            print(f"{name.upper():10} : {status}")

    def get_available(self):
        return {
            name: provider
            for name, provider in self.providers.items()
            if provider.available
        }
