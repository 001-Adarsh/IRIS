import concurrent.futures
import logging
import time
from typing import Any, Dict

from providers.gemini_provider import GeminiProvider
from providers.groq_provider import GroqProvider
from providers.ollama_provider import OllamaProvider
from providers.openrouter_provider import OpenRouterProvider

logger = logging.getLogger("AIRouter")

DEFAULT_TIMEOUT_SECONDS = 45.0
FAST_PROVIDER_TIMEOUT_SECONDS = 5.0


def _has_error(val: Any) -> bool:
    if val is None:
        return True
    s = str(val).strip().lower()
    return s.startswith(
        (
            "groq api key not configured",
            "groq execution error:",
            "gemini api key is not configured",
            "gemini error:",
            "gemini rate limited (http 429)",
            "ollama is not responding.",
            "ollama error:",
            "openrouter api key not configured",
            "openrouter execution error:",
        )
    )


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
        return {
            name: p
            for name, p in self.providers.items()
            if getattr(p, "available", False)
        }

    def compare(
        self,
        prompt: str,
        mode: str = "auto",
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> dict:
        """Route a query with a single deadline covering providers and synthesis."""
        deadline = time.monotonic() + max(timeout, 0.0)
        available = self.get_available_providers()
        if not available:
            return {
                "answers": {},
                "final": "No AI providers are available.",
                "providers": [],
            }

        is_explicit_compare = any(
            kw in prompt.lower()
            for kw in ["compare", "@council", "@compare", "consensus"]
        )
        answers = {}

        def _call_provider(name, provider):
            print(f"IRIS querying: {name.upper()}")
            req_prompt = (
                prompt[:1000]
                if name == "ollama" and len(prompt) > 1000
                else prompt
            )
            return name, provider.generate(req_prompt)

        pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=max(len(available) + 1, 4)
        )
        fast_future = None
        try:
            groq = available.get("groq")
            if (
                not is_explicit_compare
                and mode != "force_council"
                and groq
            ):
                print("\n=== IRIS AI COUNCIL: FAST PROVIDER ROUTE ===")
                fast_future = pool.submit(_call_provider, "groq", groq)
                done, _ = concurrent.futures.wait(
                    {fast_future},
                    timeout=min(
                        FAST_PROVIDER_TIMEOUT_SECONDS,
                        max(0.0, deadline - time.monotonic()) / 2,
                    ),
                )
                if done:
                    try:
                        name, result = fast_future.result()
                        if result and not _has_error(result):
                            return {
                                "answers": {name: str(result)},
                                "final": str(result),
                                "providers": [name],
                            }
                    except Exception as exc:
                        print(f"IRIS: groq error: {exc}")
                    fast_future = None

            print("\n=== IRIS AI COUNCIL: CONCURRENT EVALUATION ===")
            futures = {}
            if fast_future is not None:
                futures[fast_future] = "groq"
            futures.update(
                {
                    pool.submit(_call_provider, name, provider): name
                    for name, provider in available.items()
                    if name != "groq"
                    or is_explicit_compare
                    or mode == "force_council"
                }
            )
            if not futures:
                return {
                    "answers": {},
                    "final": "All AI Council providers failed to respond before the deadline.",
                    "providers": [],
                }

            done, pending = concurrent.futures.wait(
                futures,
                timeout=max(0.0, deadline - time.monotonic()),
            )
            for future in done:
                try:
                    name, result = future.result()
                    if result and not _has_error(result):
                        answers[name] = str(result)
                except Exception as exc:
                    print(f"IRIS: {futures[future]} error: {exc}")
            for future in pending:
                future.cancel()

            if not answers:
                return {
                    "answers": {},
                    "final": "All AI Council providers failed to respond before the deadline.",
                    "providers": [],
                }

            if len(answers) == 1:
                name, answer = next(iter(answers.items()))
                return {
                    "answers": answers,
                    "final": answer,
                    "providers": [name],
                }

            print(
                "IRIS AI Council: Synthesizing available drafts..."
            )
            synthesis_prompt = (
                "You are the Chief AI Council Judge. Synthesize the definitive answer for:\n\n"
                f"PROMPT: {prompt}\n\n"
            )
            for name, answer in answers.items():
                synthesis_prompt += f"--- {name.upper()} ---\n{answer}\n\n"
            synthesis_prompt += (
                "TASK: Merge complementary facts, eliminate errors, and provide "
                "a single polished answer."
            )

            judge_name = next(
                (
                    name
                    for name in ("groq", "gemini", "openrouter")
                    if name in available
                ),
                None,
            )
            if judge_name and time.monotonic() < deadline:
                judge_future = pool.submit(
                    available[judge_name].generate, synthesis_prompt
                )
                done, _ = concurrent.futures.wait(
                    {judge_future},
                    timeout=max(0.0, deadline - time.monotonic()),
                )
                if done:
                    try:
                        judged = judge_future.result()
                        if judged and not _has_error(judged):
                            return {
                                "answers": answers,
                                "final": str(judged),
                                "providers": list(answers),
                            }
                    except Exception as exc:
                        print(f"IRIS: {judge_name} synthesis error: {exc}")
                else:
                    judge_future.cancel()

            preferred_answer = next(
                (
                    answers[name]
                    for name in ("groq", "gemini", "openrouter")
                    if name in answers
                ),
                next(iter(answers.values())),
            )
            return {
                "answers": answers,
                "final": preferred_answer,
                "providers": list(answers),
            }
        finally:
            pool.shutdown(wait=False, cancel_futures=True)


# Backward compatibility alias
ModelRouter = AIRouter