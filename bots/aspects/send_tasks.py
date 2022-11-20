from dataclasses import dataclass
from typing import Iterable, List

from bots.db import Database


@dataclass
class SendTask:
    task_id: int
    chat_id: int
    text: str


class SendTasksStorage:
    def __init__(self, db: Database):
        self.db = db

        with self.db.with_cursor(commit=True) as c:
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS send_tasks (
                    id INTEGER PRIMARY KEY,
                    chat_id INTEGER,
                    text TEXT
                )
            """
            )

    def save(self, chat_ids: Iterable[int], text: str):
        with self.db.with_cursor(commit=True) as c:
            c.executemany(
                "INSERT INTO send_tasks (chat_id, text) VALUES (?, ?)",
                ((chat_id, text) for chat_id in chat_ids),
            )

    # def save_for_all_users(self, text: str) -> int:
    #     with self.db.with_cursor(commit=True) as c:
    #         c.execute('INSERT INTO send_tasks (chat_id, text) SELECT chat_id, ? FROM users', (text,))
    #         return c.rowcount

    def load(self, limit: int) -> List[SendTask]:
        with self.db.with_cursor() as c:
            return [
                SendTask(*row)
                for row in c.execute(
                    "SELECT id, chat_id, text FROM send_tasks ORDER BY id LIMIT ?",
                    (limit,),
                )
            ]

    def dismiss(self, ids: Iterable[int]):
        with self.db.with_cursor(commit=True) as c:
            c.executemany(
                "DELETE FROM send_tasks WHERE id = ?", ((task_id,) for task_id in ids)
            )

    def dismiss_all(self):
        with self.db.with_cursor(commit=True) as c:
            c.execute("DELETE FROM send_tasks")
