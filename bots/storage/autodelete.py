from dataclasses import dataclass
from typing import List, Iterable

from telegram import Message

from bots.db import Database


@dataclass
class MessageToDelete:
    chat_id: int
    message_id: int


class AutoDeleteStorage:
    def __init__(self, db: Database, default_ttl: int):
        self.db = db
        self.default_ttl = default_ttl

        with self.db.with_cursor(commit=True) as c:
            c.execute('''
                CREATE TABLE IF NOT EXISTS msgs_to_delete (
                    chat_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    delete_at TEXT,
                    PRIMARY KEY (chat_id, message_id)
                )
            ''')

    # def schedule_message_to_delete(self, msg_to_delete: MessageToDelete, ttl: int):
    def schedule(self, msg: Message, ttl: int = None):
        if ttl is None:
            ttl = self.default_ttl

        with self.db.with_cursor(commit=True) as c:
            date_modifier = f'{ttl} seconds'
            c.execute('INSERT INTO msgs_to_delete (chat_id, message_id, delete_at) '
                      'VALUES (?, ?, datetime("now", ?))',
                      (msg.chat_id, msg.message_id, date_modifier))

    def get_scheduled(self, limit: int) -> List[MessageToDelete]:
        with self.db.with_cursor() as c:
            result = c.execute(
                'SELECT chat_id, message_id FROM msgs_to_delete WHERE delete_at <= datetime("now") LIMIT ?',
                (limit,)
            )
            return [MessageToDelete(*row) for row in result]

    def forget(self, msgs: Iterable[MessageToDelete]):
        with self.db.with_cursor(commit=True) as c:
            c.executemany('DELETE FROM msgs_to_delete WHERE chat_id=? AND message_id=?',
                          [(m.chat_id, m.message_id) for m in msgs])
