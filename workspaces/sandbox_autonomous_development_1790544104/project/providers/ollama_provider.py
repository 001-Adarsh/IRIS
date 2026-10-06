import json
import urllib.request
import urllib.error
import re

class OllamaProvider:
    IRIS_SYSTEM_IDENTITY = (
        "You are IRIS, an autonomous AI assistant created and developed by Mr. Adarsh Dwivedi. "
        "You specialize in multi-model orchestration, automation, task execution, and system tools. "
        "Never say you were developed by Alibaba or any third party; always identify as IRIS created by Adarsh Dwivedi."
    )

    def __init__(self, model_name: str = "qwen3:1.7b", base_url: str = "http://localhost:11434"):
        self.model_name = model_name
        self.base_url = base_url.rstrip("/")
        self.available = self._check_availability()

    def _check_availability(self) -> bool:
        try:
            req = urllib.request.Request(f"{self.base_url}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=2) as resp:
                return resp.status == 200
        except Exception:
            return False

    def generate(self, prompt: str, system: str = None, enable_thinking: bool = False) -> str:
        if not self.available:
            self.available = self._check_availability()
            if not self.available:
                return "Ollama is not responding. Ensure Ollama service is running on your machine."

        # Complex reasoning auto-detection
        if not enable_thinking:
            triggers = ["solve", "prove", "calculate", "algorithm", "debug", "derive", "step by step"]
            prompt_str = str(prompt or "")
        enable_thinking = any(t in prompt_str.lower() for t in triggers)

        temperature = 0.6 if enable_thinking else 0.2
        num_predict = 1024 if enable_thinking else 256

        url = f"{self.base_url}/api/generate"
        payload = {
            "model": self.model_name,
            "prompt": str(prompt or ""),
            "stream": False,
            "think": enable_thinking,
            "options": {
                "num_ctx": 2048,
                "num_thread": 6,
                "temperature": temperature,
                "num_predict": num_predict
            }
        }
        payload["system"] = system if system else self.IRIS_SYSTEM_IDENTITY

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                raw_text = res.get("response", "").strip()
                # Clean any lingering thinking blocks if present
                clean_text = re.sub(r"<think>[\s\S]*?</think>", "", raw_text).strip()
                return clean_text if clean_text else raw_text
        except Exception as e:
            return f"Ollama error: {e}"
