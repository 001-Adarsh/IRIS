import json
import tempfile
import unittest
from pathlib import Path

from core.agent import IRISAgent


class TestAgentContext(unittest.TestCase):
    def make_agent(self, project_root):
        agent = object.__new__(IRISAgent)
        agent.project_root = Path(project_root)
        agent.chat_history = agent._load_chat_history()
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
