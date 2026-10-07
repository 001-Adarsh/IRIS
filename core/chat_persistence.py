"""SQLite persistence for email OTP challenges and user chat threads."""

import hmac
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Generator


RETENTION_SECONDS = 10 * 24 * 60 * 60
MAX_CHAT_THREADS = 5
MAX_OTP_ATTEMPTS = 5
OTP_RESEND_SECONDS = 60


class ChatLimitReached(Exception):
    """Raised when a user already owns the maximum number of saved chats."""


class OTPResendTooSoon(Exception):
    """Raised when an OTP is requested too soon after the last email."""


class ChatPersistence:
    def __init__(self, data_dir: Path):
        data_dir.mkdir(parents=True, exist_ok=True)
        self.database_path = data_dir / "chat_persistence.sqlite3"
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection, None, None]:
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS otp_challenges (
                    email TEXT PRIMARY KEY,
                    otp_digest TEXT NOT NULL,
                    sent_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS chat_threads (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL,
                    title TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS chat_threads_owner_updated
                    ON chat_threads(email, updated_at DESC);
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_id TEXT NOT NULL REFERENCES chat_threads(id)
                        ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    image_data TEXT,
                    created_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS chat_messages_thread_created
                    ON chat_messages(thread_id, id);
                """
            )

    @staticmethod
    def timestamp(epoch_seconds: int) -> str:
        return datetime.fromtimestamp(epoch_seconds, UTC).isoformat()

    def issue_otp(self, email: str, digest: str, now: int) -> None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM otp_challenges WHERE expires_at <= ?",
                (now,),
            )
            row = connection.execute(
                "SELECT sent_at FROM otp_challenges WHERE email = ?",
                (email,),
            ).fetchone()
            if row is not None and now - row["sent_at"] < OTP_RESEND_SECONDS:
                raise OTPResendTooSoon
            connection.execute(
                """
                INSERT INTO otp_challenges
                    (email, otp_digest, sent_at, expires_at, attempts)
                VALUES (?, ?, ?, ?, 0)
                ON CONFLICT(email) DO UPDATE SET
                    otp_digest = excluded.otp_digest,
                    sent_at = excluded.sent_at,
                    expires_at = excluded.expires_at,
                    attempts = 0
                """,
                (email, digest, now, now + 10 * 60),
            )

    def remove_otp(self, email: str) -> None:
        with self._connection() as connection:
            connection.execute("DELETE FROM otp_challenges WHERE email = ?", (email,))

    def verify_otp(self, email: str, digest: str, now: int) -> bool:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM otp_challenges WHERE email = ?",
                (email,),
            ).fetchone()
            if row is None:
                return False
            if row["expires_at"] <= now or row["attempts"] >= MAX_OTP_ATTEMPTS:
                connection.execute(
                    "DELETE FROM otp_challenges WHERE email = ?",
                    (email,),
                )
                return False
            if not hmac.compare_digest(row["otp_digest"], digest):
                attempts = row["attempts"] + 1
                if attempts >= MAX_OTP_ATTEMPTS:
                    connection.execute(
                        "DELETE FROM otp_challenges WHERE email = ?",
                        (email,),
                    )
                else:
                    connection.execute(
                        "UPDATE otp_challenges SET attempts = ? WHERE email = ?",
                        (attempts, email),
                    )
                return False
            connection.execute(
                "DELETE FROM otp_challenges WHERE email = ?",
                (email,),
            )
            return True

    @staticmethod
    def _prune(connection: sqlite3.Connection, now: int) -> None:
        cutoff = now - RETENTION_SECONDS
        connection.execute("DELETE FROM chat_messages WHERE created_at < ?", (cutoff,))
        connection.execute(
            "DELETE FROM chat_threads WHERE updated_at < ?",
            (cutoff,),
        )

    def list_threads(self, email: str, now: int) -> list[dict[str, Any]]:
        with self._connection() as connection:
            self._prune(connection, now)
            rows = connection.execute(
                """
                SELECT t.id, t.title, t.created_at, t.updated_at,
                    (SELECT content FROM chat_messages m
                     WHERE m.thread_id = t.id ORDER BY m.id DESC LIMIT 1)
                        AS last_message
                FROM chat_threads t
                WHERE t.email = ?
                ORDER BY t.updated_at DESC
                """,
                (email,),
            ).fetchall()
        return [
            {
                **dict(row),
                "created_at": self.timestamp(row["created_at"]),
                "updated_at": self.timestamp(row["updated_at"]),
            }
            for row in rows
        ]

    def create_thread(
        self,
        email: str,
        thread_id: str,
        now: int,
    ) -> dict[str, Any]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._prune(connection, now)
            count = connection.execute(
                "SELECT COUNT(*) AS count FROM chat_threads WHERE email = ?",
                (email,),
            ).fetchone()["count"]
            if count >= MAX_CHAT_THREADS:
                raise ChatLimitReached
            connection.execute(
                """
                INSERT INTO chat_threads (id, email, title, created_at, updated_at)
                VALUES (?, ?, 'New conversation', ?, ?)
                """,
                (thread_id, email, now, now),
            )
        return {
            "id": thread_id,
            "title": "New conversation",
            "created_at": self.timestamp(now),
            "updated_at": self.timestamp(now),
            "last_message": None,
        }

    def get_history(
        self,
        email: str,
        thread_id: str,
        now: int,
    ) -> list[dict[str, Any]] | None:
        with self._connection() as connection:
            self._prune(connection, now)
            owned = connection.execute(
                "SELECT 1 FROM chat_threads WHERE id = ? AND email = ?",
                (thread_id, email),
            ).fetchone()
            if owned is None:
                return None
            rows = connection.execute(
                """
                SELECT role, content, image_data, created_at
                FROM chat_messages WHERE thread_id = ? ORDER BY id
                """,
                (thread_id,),
            ).fetchall()
        return [
            {
                "role": row["role"],
                "content": row["content"],
                "image_data": row["image_data"],
                "timestamp": self.timestamp(row["created_at"]),
            }
            for row in rows
        ]

    def append_message(
        self,
        email: str,
        thread_id: str,
        role: str,
        content: str,
        image_data: str | None,
        now: int,
    ) -> dict[str, Any] | None:
        with self._connection() as connection:
            owned = connection.execute(
                "SELECT title FROM chat_threads WHERE id = ? AND email = ?",
                (thread_id, email),
            ).fetchone()
            if owned is None:
                return None
            connection.execute(
                """
                INSERT INTO chat_messages (thread_id, role, content, image_data, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (thread_id, role, content, image_data, now),
            )
            title = owned["title"]
            if title == "New conversation" and role == "user":
                title = content.strip().splitlines()[0][:48] or "New conversation"
                connection.execute(
                    "UPDATE chat_threads SET title = ? WHERE id = ?",
                    (title, thread_id),
                )
            connection.execute(
                "UPDATE chat_threads SET updated_at = ? WHERE id = ?",
                (now, thread_id),
            )
            row = connection.execute(
                "SELECT role, content, image_data, created_at FROM chat_messages "
                "WHERE id = last_insert_rowid()"
            ).fetchone()
        if row is None:
            raise RuntimeError("The new chat message could not be loaded.")
        return {
            "role": row["role"],
            "content": row["content"],
            "image_data": row["image_data"],
            "timestamp": self.timestamp(row["created_at"]),
        }

    def delete_thread(self, email: str, thread_id: str) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM chat_threads WHERE id = ? AND email = ?",
                (thread_id, email),
            )
        return cursor.rowcount > 0
