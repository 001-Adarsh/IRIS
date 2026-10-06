import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from core.agent import IRISAgent, IRIS_GITHUB_README_URL


class TestOwnerKnowledge(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temp_directory.name)
        data_directory = self.project_root / "data"
        data_directory.mkdir()
        (data_directory / "owner_profile.json").write_text(
            json.dumps(
                {
                    "name": "Adarsh Dwivedi",
                    "title": "Operations Team Lead - IndiaMART InterMESH Limited",
                    "role": "Operations Team Lead",
                    "company": "IndiaMART InterMESH Limited",
                    "education": "RGPV University",
                    "location": "New Delhi, India",
                    "linkedin": "https://www.linkedin.com/in/adarsh-dwivedi000/",
                    "summary": "Creator and AI Architect of IRIS.",
                    "skills": ["Operations Leadership", "Python"],
                    "projects": ["IRIS Autonomous AI Assistant"],
                }
            ),
            encoding="utf-8",
        )
        self.agent = object.__new__(IRISAgent)
        self.agent.project_root = self.project_root
        self.agent.chat_history = []
        self.agent.tools = Mock()
        self.agent.synthesizer = Mock()
        self.agent.synthesizer.synthesize.return_value = "Grounded project summary."

    def tearDown(self):
        self.temp_directory.cleanup()

    def test_owner_question_uses_configured_profile_not_model_guess(self):
        reply = self.agent.run("Tell me about Adarsh")

        self.assertIn("Operations Team Lead", reply)
        self.assertIn("IndiaMART InterMESH Limited", reply)
        self.assertIn("RGPV University", reply)
        self.assertIn("not a live verification of LinkedIn", reply)
        self.agent.synthesizer.synthesize.assert_not_called()
        self.agent.tools.execute.assert_not_called()

    def test_contextual_owner_followup_is_identified(self):
        self.assertTrue(
            self.agent._is_owner_profile_question(
                "tell me more about him",
                "tell me more about him\nASSISTANT: Adarsh Dwivedi is the owner.",
            )
        )

    def test_old_owner_mention_does_not_capture_followup_about_another_person(self):
        resolved_prompt = (
            "tell me more about him\n\n"
            "Relevant recent conversation (oldest to newest):\n"
            "USER: Tell me about Adarsh.\n"
            "ASSISTANT: Adarsh Dwivedi is IRIS's creator.\n"
            "USER: I want to know about Salman Khan.\n"
            "ASSISTANT: Salman Khan is an Indian actor.\n\n"
            "Answer the current question using this conversation."
        )

        self.assertFalse(
            self.agent._is_owner_profile_question(
                "tell me more about him", resolved_prompt
            )
        )

    def test_misspelled_read_his_linkedin_uses_owner_context(self):
        self.agent.chat_history = [
            {"role": "user", "content": "Tell me about your creator"},
            {
                "role": "assistant",
                "content": "Adarsh Dwivedi is IRIS's creator.",
            },
        ]
        self.agent.tools.execute.return_value = "LinkedIn page content."

        reply = self.agent._handle_linkedin_read_request("reah his linkedin")

        self.agent.tools.execute.assert_called_once_with(
            "read_webpage",
            url="https://www.linkedin.com/in/adarsh-dwivedi000/",
        )
        self.agent.synthesizer.synthesize.assert_called_once()
        self.assertEqual(reply, "Grounded project summary.")

    def test_common_iris_project_questions_are_identified(self):
        for prompt in (
            "What is IRIS?",
            "How does IRIS work?",
            "Tell me about your GitHub repository",
        ):
            with self.subTest(prompt=prompt):
                self.assertTrue(self.agent._is_iris_project_question(prompt))

    def test_project_question_reads_and_caches_github_readme(self):
        self.agent.tools.execute.return_value = (
            "IRIS is an evidence-based conversational AI."
        )

        reply = self.agent.run("Tell me about the IRIS project on GitHub")

        self.agent.tools.execute.assert_called_once_with(
            "read_webpage", url=IRIS_GITHUB_README_URL
        )
        self.agent.synthesizer.synthesize.assert_called_once()
        evidence = self.agent.synthesizer.synthesize.call_args.args[1]["evidence"]
        self.assertIn("IRIS is an evidence-based conversational AI.", evidence)
        self.assertEqual(reply, "Grounded project summary.")
        cached_source = (
            self.project_root
            / "data"
            / "owner_knowledge"
            / "iris_github_readme.json"
        )
        self.assertTrue(cached_source.is_file())
        self.assertIn(
            "IRIS is an evidence-based conversational AI.",
            json.loads(cached_source.read_text(encoding="utf-8"))["content"],
        )

    def test_cached_linkedin_content_is_included_in_owner_answer(self):
        self.agent._save_owner_source(
            "linkedin",
            "https://www.linkedin.com/in/adarsh-dwivedi000/",
            "LinkedIn profile details",
        )

        reply = self.agent.run("Tell me about Adarsh")

        self.assertEqual(reply, "Grounded project summary.")
        evidence = self.agent.synthesizer.synthesize.call_args.args[1]["evidence"]
        self.assertIn("LinkedIn profile details", evidence)


if __name__ == "__main__":
    unittest.main()
