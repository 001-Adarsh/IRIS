"""Persistent storage for visitor-to-owner connection requests."""

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Generator


class DuplicateConnectionError(Exception):
    """Raised when a visitor already has an active connection request."""


class LiveConnectionStore:
    def __init__(self, data_dir: Path):
        data_dir.mkdir(parents=True, exist_ok=True)
        self.database_path = data_dir / "live_connections.sqlite3"
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
                CREATE TABLE IF NOT EXISTS connections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    visitor_id TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN ('pending', 'approved', 'declined', 'closed')
                    ),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS connections_visitor_updated
                    ON connections(visitor_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS connections_status_updated
                    ON connections(status, updated_at DESC);
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_connection_per_visitor
                    ON connections(visitor_id)
                    WHERE status IN ('pending', 'approved');
                CREATE TABLE IF NOT EXISTS connection_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    connection_id INTEGER NOT NULL REFERENCES connections(id)
                        ON DELETE CASCADE,
                    sender TEXT NOT NULL CHECK (sender IN ('visitor', 'admin')),
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS connection_messages_conversation
                    ON connection_messages(connection_id, id);
                """
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat(timespec="seconds")

    @staticmethod
    def _dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    def create_request(
        self,
        visitor_id: str,
        display_name: str,
        message: str,
    ) -> dict[str, Any]:
        now = self._now()
        try:
            with self._connection() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO connections
                        (visitor_id, display_name, status, created_at, updated_at)
                    VALUES (?, ?, 'pending', ?, ?)
                    """,
                    (visitor_id, display_name, now, now),
                )
                connection.execute(
                    """
                    INSERT INTO connection_messages
                        (connection_id, sender, content, created_at)
                    VALUES (?, 'visitor', ?, ?)
                    """,
                    (cursor.lastrowid, message, now),
                )
                row = connection.execute(
                    "SELECT * FROM connections WHERE id = ?",
                    (cursor.lastrowid,),
                ).fetchone()
        except sqlite3.IntegrityError as exc:
            raise DuplicateConnectionError(
                "This browser already has a pending or approved conversation."
            ) from exc
        result = self._dict(row)
        if result is None:
            raise RuntimeError("The new connection request could not be loaded.")
        return result

    def visitor_connection(self, visitor_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM connections
                WHERE visitor_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (visitor_id,),
            ).fetchone()
        return self._dict(row)

    def get_connection(self, connection_id: int) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM connections WHERE id = ?",
                (connection_id,),
            ).fetchone()
        return self._dict(row)

    def list_connections(self, status: str | None = None) -> list[dict[str, Any]]:
        with self._connection() as connection:
            if status is None:
                rows = connection.execute(
                    """
                    SELECT c.*,
                        (SELECT content FROM connection_messages m
                         WHERE m.connection_id = c.id ORDER BY m.id DESC LIMIT 1)
                            AS last_message,
                        (SELECT id FROM connection_messages m
                         WHERE m.connection_id = c.id ORDER BY m.id DESC LIMIT 1)
                            AS last_message_id
                    FROM connections c
                    ORDER BY c.updated_at DESC
                    LIMIT 200
                    """
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT c.*,
                        (SELECT content FROM connection_messages m
                         WHERE m.connection_id = c.id ORDER BY m.id DESC LIMIT 1)
                            AS last_message,
                        (SELECT id FROM connection_messages m
                         WHERE m.connection_id = c.id ORDER BY m.id DESC LIMIT 1)
                            AS last_message_id
                    FROM connections c
                    WHERE c.status = ?
                    ORDER BY c.updated_at DESC
                    LIMIT 200
                    """,
                    (status,),
                ).fetchall()
        return [dict(row) for row in rows]

    def list_messages(
        self,
        connection_id: int,
        after_id: int = 0,
    ) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT id, sender, content, created_at
                FROM connection_messages
                WHERE connection_id = ? AND id > ?
                ORDER BY id
                LIMIT 200
                """,
                (connection_id, after_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_message(
        self,
        connection_id: int,
        sender: str,
        content: str,
    ) -> dict[str, Any] | None:
        now = self._now()
        with self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO connection_messages
                    (connection_id, sender, content, created_at)
                SELECT ?, ?, ?, ?
                WHERE EXISTS (
                    SELECT 1 FROM connections
                    WHERE id = ? AND status = 'approved'
                )
                """,
                (connection_id, sender, content, now, connection_id),
            )
            if cursor.rowcount == 0:
                return None
            connection.execute(
                "UPDATE connections SET updated_at = ? WHERE id = ?",
                (now, connection_id),
            )
            row = connection.execute(
                """
                SELECT id, sender, content, created_at
                FROM connection_messages WHERE id = ?
                """,
                (cursor.lastrowid,),
            ).fetchone()
        result = self._dict(row)
        if result is None:
            raise RuntimeError("The new conversation message could not be loaded.")
        return result

    def set_status(
        self,
        connection_id: int,
        status: str,
        *,
        expected_status: str,
    ) -> dict[str, Any] | None:
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE connections SET status = ?, updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (status, self._now(), connection_id, expected_status),
            )
            if cursor.rowcount == 0:
                return None
            row = connection.execute(
                "SELECT * FROM connections WHERE id = ?",
                (connection_id,),
            ).fetchone()
        return self._dict(row)

    def delete_connection(self, connection_id: int) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM connections WHERE id = ?",
                (connection_id,),
            )
        return cursor.rowcount > 0
