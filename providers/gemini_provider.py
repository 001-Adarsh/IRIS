import os
from typing import Any

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

    def generate_multimodal(self, history: list[dict[str, Any]]) -> str:
        """Generate an answer from the full conversation, including attached images."""
        if not self.available:
            raise RuntimeError("Image inspection is unavailable: configure GEMINI_API_KEY.")

        contents = []
        for turn in history:
            role = turn.get("role")
            if role not in {"user", "assistant"}:
                continue
            parts: list[dict[str, Any]] = [{"text": str(turn.get("content", ""))}]
            image_data = turn.get("image_data")
            if image_data:
                header, separator, encoded = image_data.partition(",")
                if not separator or not header.startswith("data:image/"):
                    raise ValueError("The attached image data URL is invalid.")
                mime_type = header[5:].split(";", 1)[0]
                parts.append(
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": encoded,
                        }
                    }
                )
            contents.append(
                {"role": "model" if role == "assistant" else "user", "parts": parts}
            )
        if not contents:
            raise ValueError("At least one user message is required for image inspection.")

        payload = {
            "system_instruction": {
                "parts": [
                    {
                        "text": (
                            "You are IRIS, an AI assistant created by Adarsh Dwivedi. "
                            "Use the full conversation to answer follow-up questions. "
                            "When an image contains UI bugs, terminal logs, tracebacks, "
                            "system errors, or anomalies, accurately extract visible "
                            "text and code, identify the likely root cause, and provide "
                            "precise diagnostic steps and a clear fix. Distinguish "
                            "visible evidence from uncertain interpretation."
                        )
                    }
                ]
            },
            "contents": contents,
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 4096},
        }
        try:
            response = requests.post(
                "https://generativelanguage.googleapis.com/v1beta/models/"
                "gemini-3.8-flash:generateContent",
                params={"key": os.environ["GEMINI_API_KEY"]},
                json=payload,
                timeout=self.TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            candidates = response.json().get("candidates", [])
            if not candidates:
                raise RuntimeError("Gemini returned no image-inspection response.")
            answer = "".join(
                part.get("text", "")
                for part in candidates[0].get("content", {}).get("parts", [])
                if isinstance(part.get("text"), str)
            ).strip()
            if not answer:
                raise RuntimeError("Gemini returned an empty image-inspection response.")
            return answer
        except requests.HTTPError as exc:
            response = exc.response
            status = response.status_code if response is not None else "unknown"
            try:
                detail = response.json().get("error", {}).get("message", "")
            except (AttributeError, ValueError):
                detail = ""
            if not detail:
                detail = "Check that the Gemini API key has access to this model."
            raise RuntimeError(
                f"Gemini image inspection failed (HTTP {status}): {detail}"
            ) from exc
        except requests.Timeout as exc:
            raise RuntimeError(
                "Gemini image inspection timed out. Try a smaller image or try again."
            ) from exc
        except requests.RequestException as exc:
            raise RuntimeError(
                "Gemini image inspection could not reach the API. Check server network access."
            ) from exc
