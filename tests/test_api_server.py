import os
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import api_server
from core.executor import IRISExecutor
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
            'data: {"choices":[{"delta":{"content":"hello"}}]}',
            "data: [DONE]",
        ]
        provider = GroqProvider(api_key="test-groq-key")

        with patch(
            "providers.groq_provider.requests.post",
            return_value=response,
        ) as post:
            tokens = list(provider.stream("Say hello"))

        self.assertEqual(tokens, ["hello"])
        self.assertEqual(
            post.call_args.kwargs["headers"]["Authorization"],
            "Bearer test-groq-key",
        )
        self.assertTrue(post.call_args.kwargs["json"]["stream"])


class TestPublicApi(unittest.TestCase):
    def setUp(self):
        api_server.limiter.reset()
        self.client = TestClient(api_server.app)

    def test_health_endpoint(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_blank_prompt_is_rejected(self):
        response = self.client.post(
            "/v1/chat/stream",
            json={"prompt": "   "},
        )
        self.assertEqual(response.status_code, 422)

    def test_chat_returns_sse_token_events(self):
        with (
            patch.object(api_server.groq_provider, "available", True),
            patch.object(
                api_server.groq_provider,
                "stream",
                return_value=iter(["Hello", " world"]),
            ),
        ):
            response = self.client.post(
                "/v1/chat/stream",
                json={"prompt": "Say hello"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn('event: token\ndata: {"token": "Hello"}', response.text)
        self.assertIn('event: done\ndata: {"done": true}', response.text)

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
            patch.object(api_server.groq_provider, "available", False),
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
