from dataclasses import dataclass
from typing import Set, List

from bots.db import Database


@dataclass
class User:
    chat_id: int
    first_name: str
    last_name: str
    username: str
    is_admin: bool


class Heb1025UsersStorage:
    def __init__(self, db: Database):
        self.db = db

        with self.db.with_cursor(commit=True) as c:
            c.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    chat_id INTEGER NOT NULL PRIMARY KEY,
                    first_name TEXT,
                    last_name TEXT,
                    username TEXT,
                    start_time TEXT NOT NULL,
                    is_admin INTEGER
                )
            ''')

    def add_user(self, chat_id: int, first_name: str, last_name: str, username: str):
        with self.db.with_cursor(commit=True) as c:
            c.execute(
                'INSERT OR REPLACE INTO users (chat_id, first_name, last_name, username, start_time, is_admin) VALUES ('
                '?, ?, ?, ?, datetime("now"), (SELECT is_admin FROM users WHERE chat_id=?)'
                ')', (chat_id, first_name, last_name, username, chat_id))

    def get_all_chat_ids(self) -> Set[int]:
        with self.db.with_cursor() as c:
            return {row[0] for row in c.execute('SELECT chat_id FROM users')}

    def get_admin_chat_ids(self) -> Set[int]:
        with self.db.with_cursor() as c:
            return {row[0] for row in c.execute('SELECT chat_id FROM users WHERE is_admin')}

    def is_admin(self, chat_id: int) -> bool:
        with self.db.with_cursor() as c:
            c.execute('SELECT is_admin FROM users WHERE chat_id=?', (chat_id,))
            row = c.fetchone()
            if row:
                return bool(row[0])
            return False

    def set_is_admin(self, chat_id: int, is_admin: bool):
        with self.db.with_cursor(commit=True) as c:
            c.execute('UPDATE users SET is_admin=? WHERE chat_id=?', (int(is_admin), chat_id))

    def get_all_users(self) -> List[User]:
        with self.db.with_cursor() as c:
            return [
                User(*row[0:4], bool(row[4]))
                for row in c.execute('''
                    SELECT chat_id, first_name, last_name, username, is_admin
                    FROM users
                    ORDER BY start_time
                ''')
            ]
