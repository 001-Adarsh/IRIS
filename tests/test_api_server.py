import os
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import api_server
from core.executor import IRISExecutor
from core.synthesizer import AISynthesizer
from providers.groq_provider import GroqProvider


class TestExecutorAdminGuard(unittest.TestCase):
    def test_public_sensitive_tool_requires_configured_admin_key(self):
        executor = IRISExecutor(object())
        plan = {
            "tools": [
                {"name": "run_powershell", "arguments": {"command": "hostname"}}
            ]
        }

        with patch.dict(os.environ, {"IRIS_ADMIN_KEY": "test-admin-key"}):
            result = executor.execute_plan(plan, is_public_request=True)

        self.assertFalse(result["success"])
        self.assertIn("Admin authorization", result["results"][0]["result"])

    def test_matching_admin_key_allows_sensitive_tool(self):
        class FakeTools:
            def list_tools(self):
                return ["run_powershell"]

            def execute(self, _tool_name, **_kwargs):
                return "ok"

        executor = IRISExecutor(FakeTools())
        plan = {
            "tools": [
                {"name": "run_powershell", "arguments": {"command": "hostname"}}
            ]
        }

        with patch.dict(os.environ, {"IRIS_ADMIN_KEY": "test-admin-key"}):
            result = executor.execute_plan(
                plan,
                is_public_request=True,
                admin_key="test-admin-key",
            )

        self.assertTrue(result["success"])


class TestGroqStreaming(unittest.TestCase):
    def test_provider_yields_sse_delta_content(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.iter_lines.return_value = [
            'data: {"choices":[{"delta":{"content":"I’m IRIS — café"}}]}'.encode(
                "utf-8"
            ),
            b"data: [DONE]",
        ]
        provider = GroqProvider(api_key="test-groq-key")

        with patch(
            "providers.groq_provider.requests.post",
            return_value=response,
        ) as post:
            tokens = list(provider.stream("Say hello"))

        self.assertEqual(tokens, ["I’m IRIS — café"])
        self.assertEqual(
            post.call_args.kwargs["headers"]["Authorization"],
            "Bearer test-groq-key",
        )
        self.assertTrue(post.call_args.kwargs["json"]["stream"])


class TestChatSynthesis(unittest.TestCase):
    def test_chat_synthesis_forces_council_and_preserves_context(self):
        synthesizer = AISynthesizer()
        synthesizer.router.compare = MagicMock(
            return_value={
                "final": "Synthesized answer",
                "providers": ["groq", "gemini"],
                "synthesized_by": "groq",
            }
        )

        result = synthesizer.synthesize_chat(
            "What about its creator?",
            [{"role": "user", "content": "Who is IRIS?"}],
        )

        self.assertEqual(result["answer"], "Synthesized answer")
        self.assertEqual(result["providers"], ["groq", "gemini"])
        prompt = synthesizer.router.compare.call_args.args[0]
        self.assertIn("USER: Who is IRIS?", prompt)
        self.assertIn("What about its creator?", prompt)
        self.assertEqual(
            synthesizer.router.compare.call_args.kwargs["mode"],
            "force_council",
        )


class TestPublicApi(unittest.TestCase):
    def setUp(self):
        api_server.limiter.reset()
        self.client = TestClient(api_server.app)

    def test_health_endpoint(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_home_serves_web_chat(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn(
            "The IRIS - Personal Assistant of Adarsh Dwivedi",
            response.text,
        )
        self.assertIn("What’s on your mind?", response.text)
        self.assertIn("data-prompt=", response.text)
        self.assertIn("New conversation", response.text)
        self.assertIn(
            'background-image: url("/assets/adarsh-dwivedi.png")',
            response.text,
        )
        self.assertIn("backdrop-filter: blur(18px)", response.text)
        self.assertNotIn("/v1/providers", response.text)
        self.assertNotIn("provider-status", response.text)
        self.assertNotIn("council-meta", response.text)
        self.assertNotIn("GROQ", response.text)
        self.assertNotIn("OPENROUTER", response.text)
        self.assertIn("conversationHistory.slice(-10)", response.text)
        self.assertIn("/v1/chat/stream", response.text)
        self.assertIn("Chat with Adarsh", response.text)
        self.assertIn("/v1/connect/requests", response.text)
        self.assertIn("height: 100dvh", response.text)
        self.assertIn("overscroll-behavior: contain", response.text)
        self.assertIn("position: sticky", response.text)
        self.assertIn('id="emoji-trigger"', response.text)
        self.assertIn('id="emoji-picker"', response.text)
        self.assertIn("emojiSelectionStart", response.text)
        self.assertIn("document.addEventListener(\"click\"", response.text)

    def test_owner_portrait_is_served_as_png(self):
        response = self.client.get("/assets/adarsh-dwivedi.png")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/png")
        self.assertTrue(response.content.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_identity_questions_use_configured_creator_without_groq(self):
        response = self.client.post(
            "/v1/chat/stream",
            json={"prompt": "Who created you?"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("IRIS was created by Adarsh Dwivedi.", response.text)
        self.assertNotIn("OpenAI", response.text)
        self.assertIn("event: done", response.text)

    def test_self_introduction_names_creator_and_assistant_capabilities(self):
        response = self.client.post(
            "/v1/chat/stream",
            json={"prompt": "Tell me about yourself and who created you"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("I'm IRIS, an AI assistant created by Adarsh Dwivedi.", response.text)
        self.assertIn("software-development tasks", response.text)

    def test_live_chat_uses_synthesizer_without_exposing_provider_names(self):
        with (
            patch.object(
                api_server.synthesizer.router,
                "get_available_providers",
                return_value={"groq": object(), "gemini": object()},
            ),
            patch.object(
                api_server.synthesizer,
                "synthesize_chat",
                return_value={
                    "answer": "A grounded answer.",
                    "providers": ["groq", "gemini"],
                    "synthesized_by": "groq",
                },
            ) as synthesize_chat,
        ):
            response = self.client.post(
                "/v1/chat/stream",
                json={
                    "prompt": "@council Tell me more about him",
                    "history": [
                        {"role": "user", "content": "Who created IRIS?"},
                        {
                            "role": "assistant",
                            "content": "Adarsh Dwivedi created IRIS.",
                        },
                    ],
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("A grounded answer.", response.text)
        self.assertNotIn("groq", response.text.lower())
        self.assertNotIn("gemini", response.text.lower())
        self.assertNotIn("openrouter", response.text.lower())
        self.assertEqual(
            synthesize_chat.call_args.args[0],
            "Tell me more about him",
        )
        self.assertEqual(
            synthesize_chat.call_args.args[1][0]["content"],
            "Who created IRIS?",
        )
        self.assertEqual(
            synthesize_chat.call_args.kwargs["creator_name"],
            "Adarsh Dwivedi",
        )

    def test_blank_prompt_is_rejected(self):
        response = self.client.post(
            "/v1/chat/stream",
            json={"prompt": "   "},
        )
        self.assertEqual(response.status_code, 422)

    def test_chat_returns_sse_token_events(self):
        with (
            patch.object(
                api_server.synthesizer.router,
                "get_available_providers",
                return_value={"groq": object()},
            ),
            patch.object(
                api_server.synthesizer,
                "synthesize_chat",
                return_value={
                    "answer": "Hello world",
                    "providers": ["groq"],
                    "synthesized_by": None,
                },
            ),
        ):
            response = self.client.post(
                "/v1/chat/stream",
                json={"prompt": "Say hello"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn('"token": "Hello world"', response.text)
        self.assertNotIn("groq", response.text.lower())
        self.assertIn('event: done\ndata: {"done": true}', response.text)

    def test_provider_status_is_not_exposed_publicly(self):
        response = self.client.get("/v1/providers")
        self.assertEqual(response.status_code, 404)

    def test_explicit_provider_route_calls_selected_provider(self):
        for provider_name in ("gemini", "openrouter"):
            with self.subTest(provider=provider_name):
                provider = MagicMock()
                provider.available = True
                provider.generate.return_value = "Direct assistant answer"
                with patch.object(
                    api_server.synthesizer.router,
                    "providers",
                    {provider_name: provider},
                ):
                    response = self.client.post(
                        "/v1/chat/stream",
                        json={
                            "prompt": (
                                f"@{provider_name} Reply with a short greeting"
                            )
                        },
                    )

                self.assertEqual(response.status_code, 200)
                self.assertIn("Direct assistant answer", response.text)
                self.assertNotIn(provider_name, response.text.lower())
                provider.generate.assert_called_once_with(
                    "Reply with a short greeting"
                )

    def test_explicit_unconfigured_provider_returns_clear_error(self):
        provider = MagicMock()
        provider.available = False
        with patch.object(
            api_server.synthesizer.router,
            "providers",
            {"gemini": provider},
        ):
            response = self.client.post(
                "/v1/chat/stream",
                json={"prompt": "@gemini Check this route"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("That assistant route is not configured.", response.text)
        provider.generate.assert_not_called()

    def test_research_uses_web_evidence_for_synthesis(self):
        with (
            patch.object(
                api_server,
                "search_web",
                return_value=[
                    {
                        "title": "Example",
                        "url": "https://example.com",
                        "snippet": "Evidence snippet",
                    }
                ],
            ),
            patch.object(
                api_server.synthesizer,
                "synthesize",
                return_value="Evidence-based answer",
            ) as synthesize,
        ):
            response = self.client.post(
                "/v1/research",
                json={"prompt": "Research this"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer"], "Evidence-based answer")
        self.assertEqual(
            response.json()["sources"][0]["url"],
            "https://example.com",
        )
        synthesize.assert_called_once()
        self.assertEqual(
            synthesize.call_args.kwargs["mode"],
            "force_council",
        )

    def test_admin_endpoint_rejects_missing_key(self):
        with patch.dict(os.environ, {"IRIS_ADMIN_KEY": "test-admin-key"}):
            response = self.client.post(
                "/v1/admin/execute",
                json={"plan": {"tools": []}},
            )
        self.assertEqual(response.status_code, 403)

    def test_sandbox_fails_closed_when_docker_is_missing(self):
        with patch.object(api_server.subprocess, "Popen", side_effect=FileNotFoundError):
            response = self.client.post(
                "/v1/execute",
                json={"code": "print('not run on host')"},
            )

        self.assertEqual(response.status_code, 503)

    def test_public_code_execution_uses_restricted_container_flags(self):
        command = api_server._sandbox_command(
            api_server.Path("C:/temporary/main.py"),
            "iris-sandbox-test",
        )
        self.assertIn("--network=none", command)
        self.assertIn("--read-only", command)
        self.assertIn("--cap-drop=ALL", command)
        self.assertIn("--memory=128m", command)
        self.assertIn("--pids-limit=32", command)

    def test_rate_limit_is_shared_across_public_routes(self):
        with (
            patch.dict(os.environ, {"IRIS_ADMIN_KEY": "test-admin-key"}),
            patch.object(
                api_server.synthesizer.router,
                "get_available_providers",
                return_value={},
            ),
        ):
            for index in range(21):
                if index % 2:
                    response = self.client.post(
                        "/v1/chat/stream",
                        json={"prompt": "test"},
                    )
                else:
                    response = self.client.post(
                        "/v1/admin/execute",
                        headers={"X-IRIS-ADMIN-KEY": "wrong-key"},
                        json={"plan": {"tools": []}},
                    )

        self.assertEqual(response.status_code, 429)


if __name__ == "__main__":
    unittest.main()
