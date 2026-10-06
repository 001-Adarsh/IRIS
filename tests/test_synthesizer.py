import unittest

from core.synthesizer import AISynthesizer


class TestAISynthesizer(unittest.TestCase):
    def test_research_prompt_requires_supported_claims_and_source_citations(self):
        prompt = AISynthesizer().build_prompt(
            "Explain the findings",
            {
                "evidence": [
                    {
                        "title": "Official documentation",
                        "url": "https://example.com/docs",
                        "findings": ["The documented feature is available."],
                    }
                ]
            },
        )

        self.assertIn("do not invent facts", prompt)
        self.assertIn("Cite researched claims inline", prompt)
        self.assertIn("https://example.com/docs", prompt)
        self.assertIn("distinguish supported facts from uncertainty", prompt)


if __name__ == "__main__":
    unittest.main()
