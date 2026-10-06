import os
import requests
from providers.base import LLMProvider


class GeminiProvider(LLMProvider):

    TIMEOUT_SECONDS = 35

    @property
    def name(self) -> str:
        return "gemini"

    @property
    def available(self) -> bool:
        return bool(os.getenv("GEMINI_API_KEY"))

    def generate(self, prompt: str) -> str:
        if not self.available:
            return "Gemini API key is not configured."

        api_key = os.environ["GEMINI_API_KEY"]
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent?key={api_key}"

        payload = {
            "contents": [
                {
                    "parts": [{"text": prompt}]
                }
            ],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 2048
            }
        }

        try:
            resp = requests.post(
                url,
                json=payload,
                timeout=self.TIMEOUT_SECONDS
            )

            if resp.status_code == 200:
                data = resp.json()
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts:
                        text = parts[0].get("text", "").strip()
                        if text:
                            return text
                return "Gemini returned an empty response."

            elif resp.status_code in (429, 503, 504):
                return "Gemini rate limited (HTTP 429)."

            return f"Gemini error: HTTP {resp.status_code}"

        except requests.exceptions.Timeout:
            return "Gemini rate limited (HTTP 429)."
        except Exception as e:
            return f"Gemini error: {e}"
