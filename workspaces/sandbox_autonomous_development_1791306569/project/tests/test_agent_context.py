import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from core.agent import IRISAgent


class TestAgentContext(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_directory.cleanup()

    def make_agent(self, project_root):
        agent = object.__new__(IRISAgent)
        agent.project_root = Path(project_root)
        agent.chat_history = agent._load_chat_history()
        return agent

    def make_source_followup_agent(self, previous_answer):
        agent = self.make_agent(self.temp_directory.name)
        agent.chat_history = [
            {"role": "user", "content": "Tell me about Adarsh."},
            {"role": "assistant", "content": previous_answer},
        ]
        agent.tools = Mock()
        agent.tools.execute.return_value = []
        return agent

    def test_pronoun_followups_include_assistant_answers(self):
        agent = object.__new__(IRISAgent)
        agent.chat_history = [
            {"role": "user", "content": "Hi Iris, who are you?"},
            {
                "role": "assistant",
                "content": "I am IRIS, created by Mr. Adarsh Dwivedi.",
            },
        ]

        for prompt in ("who is he?", "tell me more about him"):
            with self.subTest(prompt=prompt):
                resolved = agent._resolve_context(prompt)
                self.assertIn(prompt, resolved)
                self.assertIn("ASSISTANT:", resolved)
                self.assertIn("Mr. Adarsh Dwivedi", resolved)

    def test_followup_uses_conversation_in_order(self):
        agent = object.__new__(IRISAgent)
        agent.chat_history = [
            {"role": "user", "content": "Who created you?"},
            {"role": "assistant", "content": "Mr. Adarsh Dwivedi created me."},
            {"role": "user", "content": "Who is he?"},
            {
                "role": "assistant",
                "content": "He is my creator and lead engineer.",
            },
        ]

        resolved = agent._resolve_context("tell me more about him")

        self.assertLess(
            resolved.index("USER: Who created you?"),
            resolved.index("ASSISTANT: Mr. Adarsh Dwivedi created me."),
        )
        self.assertIn("ASSISTANT: He is my creator and lead engineer.", resolved)

    def test_source_question_about_previous_answer_does_not_search_web(self):
        agent = self.make_source_followup_agent(
            "Adarsh works at IndiaMART and previously worked at Example Corp."
        )

        reply = agent.run("Tell me source of information mentioned above")

        self.assertIn("did not cite any sources", reply)
        self.assertIn("should not be treated as verified", reply)
        agent.tools.execute.assert_not_called()
        self.assertEqual(agent.chat_history[-2]["role"], "user")
        self.assertEqual(
            agent.chat_history[-2]["content"],
            "Tell me source of information mentioned above",
        )

    def test_source_question_returns_only_links_in_previous_answer(self):
        agent = self.make_source_followup_agent(
            "The company bio is here: https://example.com/profile."
        )

        reply = agent.run("What is the source of the information above?")

        self.assertIn("https://example.com/profile", reply)
        self.assertIn("did not establish which claims", reply)
        agent.tools.execute.assert_not_called()

    def test_source_followup_adds_above_answer_to_context(self):
        agent = object.__new__(IRISAgent)
        agent.chat_history = [
            {"role": "assistant", "content": "Prior answer content."},
        ]

        resolved = agent._resolve_context(
            "Tell me the source of information mentioned above"
        )

        self.assertIn("ASSISTANT: Prior answer content.", resolved)

    def test_chat_history_persists_between_agent_instances(self):
        with tempfile.TemporaryDirectory() as directory:
            agent = self.make_agent(directory)
            agent._save_chat("Who are you?", "I am IRIS.")

            restored_agent = self.make_agent(directory)

            self.assertEqual(
                restored_agent.chat_history,
                [
                    {"role": "user", "content": "Who are you?"},
                    {"role": "assistant", "content": "I am IRIS."},
                ],
            )
            self.assertIn(
                "I am IRIS.",
                restored_agent._resolve_context("tell me more about her"),
            )
            saved_history = (
                Path(directory) / "data" / "chat_history.json"
            )
            self.assertTrue(saved_history.is_file())
            self.assertEqual(
                json.loads(saved_history.read_text(encoding="utf-8")),
                restored_agent.chat_history,
            )

    def test_loaded_history_is_bounded_to_recent_turns(self):
        with tempfile.TemporaryDirectory() as directory:
            history_file = Path(directory) / "data" / "chat_history.json"
            history_file.parent.mkdir(parents=True)
            history = [
                {"role": "user", "content": f"question {index}"}
                for index in range(35)
            ]
            history_file.write_text(json.dumps(history), encoding="utf-8")

            agent = self.make_agent(directory)

            self.assertEqual(len(agent.chat_history), 30)
            self.assertEqual(agent.chat_history[0]["content"], "question 5")


if __name__ == "__main__":
    unittest.main()
