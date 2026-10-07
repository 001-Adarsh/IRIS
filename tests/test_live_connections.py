import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import api_server
from core.live_connections import LiveConnectionStore


class TestLiveConnectionApi(unittest.TestCase):
    def setUp(self):
        api_server.limiter.reset()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_store = api_server.connection_store
        api_server.connection_store = LiveConnectionStore(
            api_server.Path(self.temp_dir.name)
        )
        self.admin_password = "test-admin-password-with-enough-length"
        env_patch = patch.dict(
            os.environ,
            {"IRIS_ADMIN_PASSWORD": self.admin_password},
        )
        env_patch.start()
        self.addCleanup(env_patch.stop)
        self.addCleanup(self.temp_dir.cleanup)
        self.addCleanup(
            setattr,
            api_server,
            "connection_store",
            self.original_store,
        )
        self.client = TestClient(api_server.app)

    def sign_in(self):
        return self.client.post(
            "/v1/admin/session",
            json={"password": self.admin_password},
        )

    def create_request(self, message="Please help me with a project."):
        return self.client.post(
            "/v1/connect/requests",
            json={"display_name": "Taylor", "message": message},
        )

    def test_request_is_private_until_approved_and_then_supports_two_way_chat(self):
        request = self.create_request()
        self.assertEqual(request.status_code, 200)
        self.assertEqual(request.json()["status"], "pending")
        self.assertNotIn("visitor_id", request.json())
        self.assertIn("httponly", request.headers["set-cookie"].lower())
        self.assertIn("no-store", request.headers["cache-control"])

        self.assertEqual(
            self.client.get("/v1/admin/connections").status_code,
            401,
        )
        self.assertEqual(
            self.client.post(
                "/v1/connect/messages",
                json={"message": "Before approval"},
            ).status_code,
            403,
        )

        self.assertEqual(self.sign_in().status_code, 200)
        connections = self.client.get("/v1/admin/connections?status=pending")
        self.assertEqual(connections.status_code, 200)
        connection_id = connections.json()[0]["id"]

        approval = self.client.post(
            f"/v1/admin/connections/{connection_id}/decision",
            json={"action": "approve"},
        )
        self.assertEqual(approval.status_code, 200)
        self.assertEqual(approval.json()["status"], "approved")
        self.assertEqual(
            self.client.post(
                "/v1/connect/messages",
                json={"message": "Hello Adarsh"},
            ).status_code,
            200,
        )
        owner_message = self.client.post(
            f"/v1/admin/connections/{connection_id}/messages",
            json={"message": "Hello Taylor"},
        )
        self.assertEqual(owner_message.status_code, 200)
        self.assertEqual(owner_message.json()["sender"], "admin")

        visitor_messages = self.client.get(
            "/v1/connect/messages?after_id=1"
        ).json()
        self.assertEqual(
            [message["content"] for message in visitor_messages],
            ["Hello Adarsh", "Hello Taylor"],
        )
        self.assertEqual(
            self.client.get("/v1/connect/status").json()["status"],
            "approved",
        )

    def test_login_errors_decline_and_delete_behave_safely(self):
        bad_login = self.client.post(
            "/v1/admin/session",
            json={"password": "incorrect-password"},
        )
        self.assertEqual(bad_login.status_code, 403)
        self.assertEqual(self.sign_in().status_code, 200)

        self.create_request()
        connection = self.client.get("/v1/admin/connections?status=pending").json()[0]
        connection_id = connection["id"]
        declined = self.client.post(
            f"/v1/admin/connections/{connection_id}/decision",
            json={"action": "decline"},
        )
        self.assertEqual(declined.status_code, 200)
        self.assertEqual(declined.json()["status"], "declined")

        self.assertEqual(
            self.client.post(
                f"/v1/admin/connections/{connection_id}/decision",
                json={"action": "approve"},
            ).status_code,
            409,
        )
        self.assertEqual(
            self.client.delete(
                f"/v1/admin/connections/{connection_id}"
            ).status_code,
            204,
        )
        self.assertEqual(
            self.client.get(f"/v1/admin/connections/{connection_id}/messages").status_code,
            404,
        )

    def test_only_requesting_browser_can_read_or_send_in_its_conversation(self):
        self.create_request()
        owner = TestClient(api_server.app)
        self.assertEqual(
            owner.post(
                "/v1/admin/session",
                json={"password": self.admin_password},
            ).status_code,
            200,
        )
        connection = owner.get("/v1/admin/connections?status=pending").json()[0]
        self.assertEqual(
            owner.post(
                f"/v1/admin/connections/{connection['id']}/decision",
                json={"action": "approve"},
            ).status_code,
            200,
        )

        another_visitor = TestClient(api_server.app)
        self.assertEqual(
            another_visitor.get("/v1/connect/status").json()["status"],
            "not_requested",
        )
        self.assertEqual(
            another_visitor.get("/v1/connect/messages").status_code,
            401,
        )
        self.assertEqual(
            another_visitor.post(
                "/v1/connect/messages",
                json={"message": "Not my chat"},
            ).status_code,
            401,
        )

    def test_admin_panel_is_present_but_apis_require_authentication(self):
        page = self.client.get("/admin")
        self.assertEqual(page.status_code, 200)
        self.assertIn("IRIS owner inbox", page.text)
        self.assertIn("no-store", page.headers["cache-control"])
        admin_script = self.client.get("/assets/admin.js")
        self.assertEqual(admin_script.status_code, 200)
        self.assertNotIn("window.alert", admin_script.text)
        self.assertIn("Cannot reach IRIS right now", admin_script.text)
        self.assertIn("refreshInProgress", admin_script.text)
        self.assertEqual(
            self.client.get("/v1/admin/connections").status_code,
            401,
        )
        self.assertEqual(
            self.client.get("/v1/connect/status").json(),
            {"status": "not_requested", "messages": []},
        )

    def test_requests_fail_closed_until_a_strong_admin_password_is_configured(self):
        with patch.dict(os.environ, {"IRIS_ADMIN_PASSWORD": ""}):
            response = self.client.post(
                "/v1/connect/requests",
                json={"message": "Can I chat with you?"},
            )
            self.assertEqual(response.status_code, 503)
            self.assertEqual(
                self.client.get("/v1/admin/connections").status_code,
                401,
            )
        self.assertEqual(api_server.connection_store.list_connections(), [])


if __name__ == "__main__":
    unittest.main()
