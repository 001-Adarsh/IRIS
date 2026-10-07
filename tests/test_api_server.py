import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests
from fastapi.testclient import TestClient

import api_server
from core.executor import IRISExecutor
from core.chat_persistence import ChatPersistence
from core.synthesizer import AISynthesizer
from providers.gemini_provider import GeminiProvider
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


class TestGeminiMultimodal(unittest.TestCase):
    def test_image_and_full_turn_history_are_sent_to_a_vision_model(self):
        response = MagicMock()
        response.json.return_value = {
            "candidates": [
                {"content": {"parts": [{"text": "The error is a missing import."}]}}
            ]
        }
        provider = GeminiProvider()
        history = [
            {"role": "user", "content": "Here is a screenshot.", "image_data": None},
            {
                "role": "assistant",
                "content": "I can inspect it.",
                "image_data": None,
            },
            {
                "role": "user",
                "content": "What is this traceback?",
                "image_data": "data:image/png;base64,aGVsbG8=",
            },
        ]

        with (
            patch.dict(os.environ, {"GEMINI_API_KEY": "test-gemini-key"}),
            patch(
                "providers.gemini_provider.requests.post",
                return_value=response,
            ) as post,
        ):
            answer = provider.generate_multimodal(history)

        payload = post.call_args.kwargs["json"]
        self.assertEqual(answer, "The error is a missing import.")
        self.assertIn("gemini-3.8-flash:generateContent", post.call_args.args[0])
        self.assertEqual(len(payload["contents"]), 3)
        self.assertEqual(
            payload["contents"][2]["parts"][1]["inline_data"],
            {"mime_type": "image/png", "data": "aGVsbG8="},
        )
        self.assertIn("root cause", payload["system_instruction"]["parts"][0]["text"])

    def test_http_error_explains_vision_api_failure_without_falling_back_to_generic(self):
        response = MagicMock()
        response.status_code = 404
        response.json.return_value = {
            "error": {"message": "The requested vision model is unavailable."}
        }
        error = requests.HTTPError("Not found")
        error.response = response
        provider = GeminiProvider()

        with (
            patch.dict(os.environ, {"GEMINI_API_KEY": "test-gemini-key"}),
            patch(
                "providers.gemini_provider.requests.post",
                side_effect=error,
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                r"Gemini image inspection failed \(HTTP 404\): The requested vision model is unavailable\.",
            ):
                provider.generate_multimodal(
                    [
                        {
                            "role": "user",
                            "content": "Inspect this image.",
                            "image_data": "data:image/png;base64,aGVsbG8=",
                        }
                    ]
                )


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

    def test_chat_synthesis_keeps_history_beyond_the_previous_ten_turn_limit(self):
        synthesizer = AISynthesizer()
        synthesizer.router.compare = MagicMock(
            return_value={"final": "Answer", "providers": ["groq"]}
        )
        history = [
            {"role": "user", "content": f"Question {index}"}
            for index in range(15)
        ]

        synthesizer.synthesize_chat("Follow up", history)

        prompt = synthesizer.router.compare.call_args.args[0]
        self.assertIn("USER: Question 0", prompt)
        self.assertIn("USER: Question 14", prompt)
        self.assertIn("FULL CONVERSATION:", prompt)


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
        self.assertIn("iris-chat-cache:", response.text)
        self.assertIn("Date.now() - saved.savedAt < CACHE_TTL", response.text)
        self.assertIn("Continue without signing in", response.text)
        self.assertIn('guestMode = true', response.text)
        self.assertIn('"/v1/chat/stream"', response.text)
        self.assertIn("/api/chats/", response.text)
        self.assertIn("/auth/send-otp", response.text)
        self.assertIn("/auth/verify-otp", response.text)
        self.assertIn('accept="image/png,image/jpeg,image/webp,.png,.jpg,.jpeg,.webp"', response.text)
        self.assertIn("Chat with Adarsh", response.text)
        self.assertIn("/v1/connect/requests", response.text)
        self.assertIn("height: 100dvh", response.text)
        self.assertIn("overscroll-behavior: contain", response.text)
        self.assertIn("position: sticky", response.text)
        self.assertIn('id="emoji-trigger"', response.text)
        self.assertIn('id="emoji-picker"', response.text)
        self.assertIn("emojiSelectionStart", response.text)
        self.assertIn("document.addEventListener(\"click\"", response.text)


class TestAuthenticatedChatApi(unittest.TestCase):
    def setUp(self):
        api_server.limiter.reset()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_store = api_server.chat_store
        api_server.chat_store = ChatPersistence(Path(self.temp_dir.name))
        self.client = TestClient(api_server.app)
        self.environment = patch.dict(
            os.environ,
            {
                "IRIS_AUTH_SECRET": "test-auth-secret-long-enough-for-hmac-32",
                "SENDGRID_API_KEY": "test-sendgrid-key",
                "IRIS_FROM_EMAIL": "no-reply@example.com",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def tearDown(self):
        api_server.chat_store = self.original_store
        self.temp_dir.cleanup()

    def _signed_in_client(self, email="person@example.com"):
        with (
            patch.object(api_server.secrets, "randbelow", return_value=7),
            patch(
                "api_server.requests.post",
                return_value=MagicMock(status_code=202),
            ) as send_email,
        ):
            sent = self.client.post("/auth/send-otp", json={"email": email})
        self.assertEqual(sent.status_code, 200)
        self.assertIn("000007", send_email.call_args.kwargs["json"]["content"][0]["value"])
        verified = self.client.post(
            "/auth/verify-otp",
            json={"email": email, "code": "000007"},
        )
        self.assertEqual(verified.status_code, 200)
        return {"Authorization": f"Bearer {verified.json()['token']}"}

    def test_email_otp_issues_a_signed_token_for_saved_chat_apis(self):
        headers = self._signed_in_client("Person@Example.com")
        self.assertEqual(
            self.client.get("/api/chats", headers=headers).json(),
            [],
        )
        created = self.client.post("/api/chats", headers=headers)
        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json()["title"], "New conversation")
        self.assertEqual(
            self.client.get(
                f"/api/chats/{created.json()['id']}",
                headers=headers,
            ).json()["messages"],
            [],
        )

    def test_otp_is_one_time_and_rejected_after_wrong_attempts(self):
        with patch.object(api_server.secrets, "randbelow", return_value=7), patch(
            "api_server.requests.post",
            return_value=MagicMock(status_code=202),
        ):
            self.client.post("/auth/send-otp", json={"email": "person@example.com"})
        wrong = self.client.post(
            "/auth/verify-otp",
            json={"email": "person@example.com", "code": "000008"},
        )
        self.assertEqual(wrong.status_code, 401)
        correct = self.client.post(
            "/auth/verify-otp",
            json={"email": "person@example.com", "code": "000007"},
        )
        self.assertEqual(correct.status_code, 200)
        reused = self.client.post(
            "/auth/verify-otp",
            json={"email": "person@example.com", "code": "000007"},
        )
        self.assertEqual(reused.status_code, 401)

    def test_sixth_chat_requires_deleting_an_existing_thread(self):
        headers = self._signed_in_client()
        created = [
            self.client.post("/api/chats", headers=headers).json()
            for _ in range(5)
        ]
        sixth = self.client.post("/api/chats", headers=headers)
        self.assertEqual(sixth.status_code, 409)
        self.client.delete(f"/api/chats/{created[0]['id']}", headers=headers)
        self.assertEqual(
            self.client.post("/api/chats", headers=headers).status_code,
            200,
        )

    def test_saved_chat_stream_persists_full_context_and_image(self):
        headers = self._signed_in_client()
        created = self.client.post("/api/chats", headers=headers).json()
        thread_id = created["id"]
        api_server.chat_store.append_message(
            "person@example.com",
            thread_id,
            "user",
            "Earlier question",
            None,
            int(api_server.time.time()),
        )
        api_server.chat_store.append_message(
            "person@example.com",
            thread_id,
            "assistant",
            "Earlier answer",
            None,
            int(api_server.time.time()),
        )
        vision = MagicMock(available=True)
        vision.generate_multimodal.return_value = "The screenshot shows a traceback."
        with patch.dict(
            api_server.synthesizer.router.providers,
            {"gemini": vision},
            clear=False,
        ):
            response = self.client.post(
                f"/api/chats/{thread_id}/stream",
                headers=headers,
                json={
                    "prompt": "What is the error?",
                    "image_data": "data:image/png;base64,aGVsbG8=",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("The screenshot shows a traceback.", response.text)
        vision.generate_multimodal.assert_called_once()
        self.assertEqual(
            [turn["content"] for turn in vision.generate_multimodal.call_args.args[0]],
            ["Earlier question", "Earlier answer", "What is the error?"],
        )
        history = self.client.get(
            f"/api/chats/{thread_id}",
            headers=headers,
        ).json()["messages"]
        self.assertEqual(len(history), 4)
        self.assertEqual(history[-2]["image_data"], "data:image/png;base64,aGVsbG8=")
        self.assertTrue(history[-1]["timestamp"])

    def test_saved_chat_tokens_cannot_read_other_users_threads(self):
        first = self._signed_in_client("one@example.com")
        second = self._signed_in_client("two@example.com")
        thread = self.client.post("/api/chats", headers=first).json()
        response = self.client.get(f"/api/chats/{thread['id']}", headers=second)
        self.assertEqual(response.status_code, 404)

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

    def test_guest_chat_can_submit_images_and_the_full_context(self):
        vision = MagicMock(available=True)
        vision.generate_multimodal.return_value = "I can see the screenshot."
        previous_turn = {
            "role": "user",
            "content": "Earlier prompt.",
            "image_data": None,
        }
        with patch.dict(
            api_server.synthesizer.router.providers,
            {"gemini": vision},
            clear=False,
        ):
            response = self.client.post(
                "/v1/chat/stream",
                json={
                    "prompt": "What error is shown?",
                    "history": [previous_turn],
                    "image_data": "data:image/png;base64,aGVsbG8=",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("I can see the screenshot.", response.text)
        self.assertEqual(
            [turn["content"] for turn in vision.generate_multimodal.call_args.args[0]],
            ["Earlier prompt.", "What error is shown?"],
        )
        self.assertEqual(
            vision.generate_multimodal.call_args.args[0][-1]["image_data"],
            "data:image/png;base64,aGVsbG8=",
        )

    def test_guest_image_validation_rejects_invalid_data_urls(self):
        response = self.client.post(
            "/v1/chat/stream",
            json={
                "prompt": "Inspect this.",
                "image_data": "data:image/svg+xml;base64,PHN2Zz4=",
            },
        )
        self.assertEqual(response.status_code, 422)

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
