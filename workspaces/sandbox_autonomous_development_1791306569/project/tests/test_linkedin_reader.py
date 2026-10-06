import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from core.agent import IRISAgent, LINKEDIN_PROFILE_URL


class TestLinkedInReader(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_directory.cleanup()

    def make_agent(self, page_content):
        agent = object.__new__(IRISAgent)
        agent.project_root = Path(self.temp_directory.name)
        agent.chat_history = []
        agent.tools = Mock()
        agent.tools.execute.return_value = page_content
        agent.synthesizer = Mock()
        agent.synthesizer.synthesize.return_value = "Profile summary."
        return agent

    def test_read_my_linkedin_page_uses_known_profile_and_summarizes(self):
        agent = self.make_agent("Public profile content")

        reply = agent.run("Read my LinkedInn page")

        agent.tools.execute.assert_called_once_with(
            "read_webpage", url=LINKEDIN_PROFILE_URL
        )
        agent.synthesizer.synthesize.assert_called_once()
        evidence = agent.synthesizer.synthesize.call_args.args[1]["evidence"]
        self.assertIn("Public profile content", evidence)
        self.assertEqual(reply, "Profile summary.")

    def test_explicit_linkedin_url_is_used_without_trailing_punctuation(self):
        agent = self.make_agent("Public profile content")

        agent._handle_linkedin_read_request(
            "Please read https://www.linkedin.com/in/example-profile/."
        )

        agent.tools.execute.assert_called_once_with(
            "read_webpage",
            url="https://www.linkedin.com/in/example-profile/",
        )

    def test_unknown_profile_requires_url_instead_of_guessing(self):
        agent = self.make_agent("Public profile content")

        reply = agent._handle_linkedin_read_request("Read the LinkedIn page")

        self.assertIn("Please share the LinkedIn profile URL", reply)
        agent.tools.execute.assert_not_called()
        agent.synthesizer.synthesize.assert_not_called()

    def test_reader_error_explains_public_access_limitation(self):
        agent = self.make_agent("Web reader error: blocked")

        with patch(
            "core.agent.read_authenticated_page",
            side_effect=RuntimeError("Edge could not start"),
        ):
            reply = agent._handle_linkedin_read_request(
                "Read my LinkedIn page"
            )

        self.assertIn("couldn't access", reply)
        self.assertIn("Reader detail: Web reader error: blocked", reply)
        self.assertIn("Browser detail: RuntimeError: Edge could not start", reply)
        agent.synthesizer.synthesize.assert_not_called()

    def test_login_wall_opens_managed_browser_and_requests_one_confirmation(self):
        agent = self.make_agent("Sign in to view this profile")

        with patch(
            "core.agent.read_authenticated_page",
            return_value="Sign in to view this profile",
        ) as read_browser:
            reply = agent._handle_linkedin_read_request("Read my LinkedIn page")

        read_browser.assert_called_once_with(
            LINKEDIN_PROFILE_URL,
            agent.project_root / "data" / "iris_browser_profile",
        )
        self.assertIn("Microsoft Edge window", reply)
        self.assertIn("type `done`", reply)
        self.assertIn("doesn't capture your password", reply)
        self.assertEqual(agent._pending_browser_url, LINKEDIN_PROFILE_URL)
        agent.synthesizer.synthesize.assert_not_called()

    def test_non_linkedin_login_wall_never_requests_credentials(self):
        agent = self.make_agent("HTTP 401 Unauthorized")

        with patch(
            "core.agent.read_authenticated_page",
            return_value="Sign in to continue",
        ) as read_browser:
            reply = agent._handle_linkedin_read_request(
                "Read https://example.com/private"
            )

        read_browser.assert_called_once_with(
            "https://example.com/private",
            agent.project_root / "data" / "iris_browser_profile",
        )
        agent.tools.execute.assert_called_once_with(
            "read_webpage", url="https://example.com/private"
        )
        self.assertIn("requires sign-in", reply)
        self.assertIn("type `done`", reply)
        agent.synthesizer.synthesize.assert_not_called()

    def test_done_after_sign_in_reads_the_same_browser_session(self):
        agent = self.make_agent("Sign in to view this profile")
        with patch(
            "core.agent.read_authenticated_page",
            side_effect=[
                "Sign in to view this profile",
                "Adarsh Dwivedi\nSoftware Engineer\nExperience: Example Corp",
            ],
        ) as read_browser:
            initial_reply = agent._handle_linkedin_read_request(
                "Read my LinkedIn page"
            )
            reply = agent._handle_linkedin_read_request("done")

        self.assertIn("type `done`", initial_reply)
        agent.tools.execute.assert_called_once_with(
            "read_webpage", url=LINKEDIN_PROFILE_URL
        )
        self.assertEqual(read_browser.call_count, 2)
        self.assertEqual(
            read_browser.call_args.args[0],
            LINKEDIN_PROFILE_URL,
        )
        agent.synthesizer.synthesize.assert_called_once()
        evidence = agent.synthesizer.synthesize.call_args.args[1]["evidence"]
        self.assertIn("Adarsh Dwivedi", evidence)
        self.assertEqual(
            agent.synthesizer.synthesize.call_args.args[0],
            "Read my LinkedIn page",
        )
        self.assertEqual(reply, "Profile summary.")
        self.assertIsNone(agent._pending_browser_url)

    def test_non_linkedin_public_page_can_be_summarized(self):
        agent = self.make_agent("Public article content")

        reply = agent._handle_linkedin_read_request(
            "Summarize https://example.com/article"
        )

        agent.tools.execute.assert_called_once_with(
            "read_webpage", url="https://example.com/article"
        )
        self.assertEqual(reply, "Profile summary.")
        agent.synthesizer.synthesize.assert_called_once()

    def test_raw_page_content_is_returned_if_ai_summary_is_unavailable(self):
        agent = self.make_agent("Public profile content")
        agent.synthesizer.synthesize.return_value = (
            "IRIS could not synthesize the research."
        )

        reply = agent._handle_linkedin_read_request("Read my LinkedIn page")

        self.assertIn("Content retrieved from", reply)
        self.assertIn("Public profile content", reply)


if __name__ == "__main__":
    unittest.main()
