from dataclasses import dataclass
from typing import Optional

from bots.db import Database


@dataclass
class UnconfirmedText:
    id: int
    text: str
    confirmation_message_id: Optional[int]


class Storage:
    def __init__(self, db: Database):
        self.db = db

        with self.db.with_cursor(commit=True) as c:
            c.execute('''
                CREATE TABLE IF NOT EXISTS unconfirmed_texts (
                    id INTEGER PRIMARY KEY,
                    text TEXT,
                    confirmation_message_id INTEGER
                )
            ''')

    def save_text(self, text: str) -> int:
        with self.db.with_cursor(commit=True) as c:
            c.execute('INSERT INTO unconfirmed_texts (text) VALUES (?)', (text,))
            return c.lastrowid

    def save_confirmation_message_id(self, text_id: int, confirmation_message_id: int):
        with self.db.with_cursor(commit=True) as c:
            c.execute('UPDATE unconfirmed_texts SET confirmation_message_id=? WHERE id=?',
                      (confirmation_message_id, text_id))

    def load(self, text_id: int) -> UnconfirmedText:
        with self.db.with_cursor() as c:
            c.execute('SELECT text, confirmation_message_id FROM unconfirmed_texts WHERE id=?', (text_id,))
            row = c.fetchone()
            if row:
                return UnconfirmedText(text_id, row[0], row[1])

    def delete(self, text_id: int):
        with self.db.with_cursor(commit=True) as c:
            c.execute('DELETE FROM unconfirmed_texts WHERE id=?', (text_id,))
