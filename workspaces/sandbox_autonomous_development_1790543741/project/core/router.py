from typing import Dict, Any, List
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError

from providers.groq_provider import GroqProvider
from providers.gemini_provider import GeminiProvider
from providers.ollama_provider import OllamaProvider
from providers.openrouter_provider import OpenRouterProvider


def _has_error(val: Any) -> bool:
    if val is None:
        return True
    s = str(val).lower()
    return any(err in s for err in ["error", "429", "rate limit", "timed out", "connection error"])


class AIRouter:
    """Routes prompts to available model providers and handles consensus."""

    def __init__(self):
        self.providers = {
            "groq": GroqProvider(),
            "gemini": GeminiProvider(),
            "ollama": OllamaProvider(),
            "openrouter": OpenRouterProvider(),
        }

    def get_available_providers(self) -> Dict[str, Any]:
        """Returns active and ready providers."""
        return {name: p for name, p in self.providers.items() if getattr(p, "available", False)}

    def compare(self, prompt: str, mode: str = "auto") -> dict:
        """Fast-path direct routing for general queries; parallel consensus for explicit comparisons."""
        groq = self.providers.get("groq")
        is_explicit_compare = any(kw in prompt.lower() for kw in ["compare", "@council", "@compare", "consensus"])

        # FAST PATH: Instant Groq LPU return (~0.4s) for everyday tasks
        if not is_explicit_compare and mode != "force_council" and groq and groq.available:
            try:
                ans = groq.generate(prompt)
                if ans and not _has_error(ans):
                    return {"answers": {"groq": str(ans)}, "final": str(ans), "providers": ["groq"]}
            except Exception:
                pass

        available = self.get_available_providers()
        if not available:
            fallback = self.providers.get("openrouter") or self.providers.get("ollama")
            ans = fallback.generate(prompt) if fallback else "No providers available"
            return {"answers": {}, "final": ans, "providers": []}

        answers = {}
        print("\n=== IRIS AI COUNCIL: CONCURRENT EVALUATION ===")

        def _call_provider(name, provider):
            print(f"IRIS querying: {name.upper()}")
            req_prompt = prompt[:1000] if name == "ollama" and len(prompt) > 1000 else prompt
            return name, provider.generate(req_prompt)

        # Dynamic worker count avoids blocking any registered provider
        workers = max(len(available), 4)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_call_provider, k, v): k for k, v in available.items()}
            try:
                # 8.0s timeout ensures cloud API roundtrips (Gemini/OpenRouter) complete reliably
                for f in as_completed(futures, timeout=8.0):
                    try:
                        k, res = f.result()
                        if res and not _has_error(res):
                            answers[k] = str(res)
                    except Exception as e:
                        print(f"IRIS: {futures[f]} error: {e}")
            except TimeoutError:
                print("IRIS AI Council: Fast consensus deadline reached. Synthesizing available drafts...")

        if not answers:
            # Fallback attempts across primary fast APIs
            for fallback_name in ["groq", "openrouter", "gemini"]:
                fb = self.providers.get(fallback_name)
                if fb and fb.available:
                    try:
                        ans = fb.generate(prompt)
                        if ans and not _has_error(ans):
                            return {"answers": {fallback_name: ans}, "final": ans, "providers": [fallback_name]}
                    except Exception:
                        continue
            return {"answers": {}, "final": "All AI Council providers failed to respond.", "providers": []}

        if len(answers) == 1:
            k = list(answers.keys())[0]
            return {"answers": answers, "final": answers[k], "providers": [k]}

        # Chief Judge Synthesis
        print("IRIS AI Council: Synthesizing multi-model consensus...")
        synthesis_prompt = f"You are the Chief AI Council Judge. Synthesize the definitive answer for:\n\nPROMPT: {prompt}\n\n"
        for name, ans in answers.items():
            synthesis_prompt += f"--- {name.upper()} ---\n{ans}\n\n"
        synthesis_prompt += "TASK: Merge complementary facts, eliminate errors, and provide a single polished answer."

        # Adaptive judge hierarchy: Groq -> Gemini -> OpenRouter
        judge_candidates = [
            self.providers.get("groq"),
            self.providers.get("gemini"),
            self.providers.get("openrouter")
        ]
        
        final_answer = ""
        for judge in judge_candidates:
            if judge and judge.available:
                try:
                    res = judge.generate(synthesis_prompt)
                    if res and not _has_error(res):
                        final_answer = str(res)
                        break
                except Exception:
                    continue

        if not final_answer or _has_error(final_answer):
            final_answer = (
                answers.get("groq")
                or answers.get("gemini")
                or answers.get("openrouter")
                or list(answers.values())[0]
            )

        return {"answers": answers, "final": final_answer, "providers": list(answers.keys())}


# Backward compatibility alias
ModelRouter = AIRouter