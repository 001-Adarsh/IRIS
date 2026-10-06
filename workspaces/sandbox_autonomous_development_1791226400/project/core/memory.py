import sqlite3
from pathlib import Path
from datetime import datetime


DB_PATH = (
    Path(__file__).resolve().parent.parent
    / "data"
    / "iris_memory.db"
)


class Memory:

    def __init__(self):
        DB_PATH.parent.mkdir(exist_ok=True)
        self.create_database()

    def connect(self):
        return sqlite3.connect(DB_PATH)

    def create_database(self):

        with self.connect() as conn:

            conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)

            conn.commit()

    def save(self, content):

        with self.connect() as conn:

            conn.execute(
                """
                INSERT INTO memories
                (content, created_at)
                VALUES (?, ?)
                """,
                (
                    content,
                    datetime.now().isoformat()
                )
            )

            conn.commit()

        return "Memory saved."

    def search(self, query, limit=10):

        with self.connect() as conn:

            rows = conn.execute(
                """
                SELECT id, content, created_at
                FROM memories
                WHERE content LIKE ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (
                    f"%{query}%",
                    limit
                )
            ).fetchall()

        return rows

    def list_memories(self, limit=20):

        with self.connect() as conn:

            rows = conn.execute(
                """
                SELECT id, content, created_at
                FROM memories
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,)
            ).fetchall()

        return rows
