import os
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

# Force load the .env file from the project root
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path)
load_dotenv()  # Fallback to current working directory

class OpenRouterProvider:
    def __init__(self, api_key: str = None, default_model: str = "openrouter/free"):
        self.api_key = (api_key or os.getenv("OPENROUTER_API_KEY") or "").strip()
        self.default_model = default_model
        self.available = bool(self.api_key and self.api_key.startswith("sk-or-v1-"))
        
        if self.available:
            self.client = OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=self.api_key
            )
        else:
            self.client = None

    def generate(self, prompt: str, system: str = None, model: str = None) -> str:
        if not self.available or not self.client:
            return "OpenRouter API key not configured."

        target_model = model or self.default_model
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        try:
            response = self.client.chat.completions.create(
                model=target_model,
                messages=messages,
                timeout=20
            )
            return response.choices[0].message.content
        except Exception as e:
            return f"OpenRouter execution error: {str(e)}"