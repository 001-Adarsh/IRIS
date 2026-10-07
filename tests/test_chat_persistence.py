import tempfile
import unittest
from pathlib import Path

from core.chat_persistence import (
    ChatLimitReached,
    ChatPersistence,
    OTPResendTooSoon,
    RETENTION_SECONDS,
)


class TestChatPersistence(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = ChatPersistence(Path(self.temp_dir.name))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_threads_and_messages_are_private_to_their_email_owner(self):
        now = 1_800_000_000
        thread = self.store.create_thread("one@example.com", "thread-1", now)
        self.store.append_message(
            "one@example.com", thread["id"], "user", "Hello", None, now + 1
        )

        self.assertEqual(
            self.store.get_history("one@example.com", thread["id"], now + 1)[0][
                "content"
            ],
            "Hello",
        )
        self.assertIsNone(
            self.store.get_history("two@example.com", thread["id"], now + 1)
        )
        self.assertEqual(
            self.store.list_threads("one@example.com", now + 1)[0]["title"],
            "Hello",
        )

    def test_user_cannot_create_a_sixth_saved_chat(self):
        now = 1_800_000_000
        for index in range(5):
            self.store.create_thread("one@example.com", f"thread-{index}", now)

        with self.assertRaises(ChatLimitReached):
            self.store.create_thread("one@example.com", "thread-5", now)

        self.store.delete_thread("one@example.com", "thread-0")
        self.store.create_thread("one@example.com", "thread-5", now)
        self.assertEqual(len(self.store.list_threads("one@example.com", now)), 5)

    def test_old_messages_and_idle_threads_expire_after_ten_days(self):
        now = 1_800_000_000
        thread = self.store.create_thread("one@example.com", "thread-1", now)
        self.store.append_message(
            "one@example.com", thread["id"], "user", "Old", None, now
        )
        self.store.append_message(
            "one@example.com",
            thread["id"],
            "assistant",
            "Recent",
            None,
            now + RETENTION_SECONDS - 5,
        )

        history = self.store.get_history(
            "one@example.com",
            thread["id"],
            now + RETENTION_SECONDS + 1,
        )
        self.assertEqual([message["content"] for message in history], ["Recent"])
        self.assertEqual(
            self.store.list_threads(
                "one@example.com",
                now + RETENTION_SECONDS * 2,
            ),
            [],
        )

    def test_otp_is_single_use_expiring_and_resend_limited(self):
        now = 1_800_000_000
        self.store.issue_otp("one@example.com", "digest", now)
        with self.assertRaises(OTPResendTooSoon):
            self.store.issue_otp("one@example.com", "next", now + 10)
        self.assertFalse(self.store.verify_otp("one@example.com", "wrong", now + 1))
        self.assertTrue(self.store.verify_otp("one@example.com", "digest", now + 2))
        self.assertFalse(self.store.verify_otp("one@example.com", "digest", now + 3))

        self.store.issue_otp("one@example.com", "expired", now + 120)
        self.assertFalse(
            self.store.verify_otp(
                "one@example.com",
                "expired",
                now + 120 + 10 * 60,
            )
        )


if __name__ == "__main__":
    unittest.main()
