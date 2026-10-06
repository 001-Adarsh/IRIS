import time
import unittest

from core.router import AIRouter


class FakeProvider:
    available = True

    def __init__(self, name, judge_delay=0):
        self.name = name
        self.judge_delay = judge_delay

    def generate(self, prompt):
        if "Chief AI Council Judge" in prompt and self.judge_delay:
            time.sleep(self.judge_delay)
        return f"answer from {self.name}"


class BlockingProvider(FakeProvider):
    def generate(self, prompt):
        time.sleep(0.2)
        return super().generate(prompt)


class TestAIRouter(unittest.TestCase):
    def test_fast_provider_timeout_does_not_block_caller(self):
        router = object.__new__(AIRouter)
        router.providers = {"groq": BlockingProvider("groq")}

        started = time.monotonic()
        result = router.compare("ordinary request", timeout=0.03)
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 0.15)
        self.assertIn("deadline", result["final"])

    def test_synthesis_uses_remaining_deadline_and_returns_a_draft(self):
        router = object.__new__(AIRouter)
        router.providers = {
            "groq": FakeProvider("groq", judge_delay=0.2),
            "gemini": FakeProvider("gemini"),
        }

        started = time.monotonic()
        result = router.compare("@council compare these", timeout=0.04)
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 0.15)
        self.assertEqual(result["final"], "answer from groq")
        self.assertEqual(set(result["providers"]), {"groq", "gemini"})


if __name__ == "__main__":
    unittest.main()
