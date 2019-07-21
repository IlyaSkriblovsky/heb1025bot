import sqlite3
from dataclasses import dataclass
from threading import Lock
from typing import Set, List, Iterable, Optional


@dataclass
class SendTask:
    task_id: int
    chat_id: int
    text: str


@dataclass
class UnconfirmedText:
    id: int
    text: str
    confirmation_message_id: Optional[int]


@dataclass
class MessageToDelete:
    chat_id: int
    message_id: int


class Serializer:
    def __init__(self, lock: Lock, cursor_getter):
        self.lock = lock
        self.cursor_getter = cursor_getter

    def __enter__(self):
        self.lock.acquire()
        self.cursor = self.cursor_getter()
        return self.cursor

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cursor.close()
        self.lock.release()


class Storage:
    sq_conn = None

    def __init__(self):
        self.lock = Lock()

    def __initialize(self):
        if self.sq_conn is None:
            self.sq_conn = sqlite3.connect('./data.sqlite3', check_same_thread=False)
            self.create_tables()

    def create_tables(self):
        c = self.sq_conn.cursor()
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
        c.execute('''
            CREATE TABLE IF NOT EXISTS msgs_to_delete (
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                delete_at TEXT,
                PRIMARY KEY (chat_id, message_id)
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS unconfirmed_texts (
                id INTEGER PRIMARY KEY,
                text TEXT,
                confirmation_message_id INTEGER
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS send_tasks (
                id INTEGER PRIMARY KEY,
                chat_id INTEGER,
                text TEXT
            )
        ''')
        self.sq_conn.commit()

    def _get_cursor(self):
        self.__initialize()
        return self.sq_conn.cursor()

    def with_cursor(self):
        return Serializer(self.lock, self._get_cursor)

    def add_user(self, chat_id: int, first_name: str, last_name: str, username: str):
        with self.with_cursor() as c:
            c.execute(
                'INSERT OR REPLACE INTO users (chat_id, first_name, last_name, username, start_time, is_admin) VALUES ('
                '?, ?, ?, ?, datetime("now"), (SELECT is_admin FROM users WHERE chat_id=?)'
                ')', (chat_id, first_name, last_name, username, chat_id))
            self.sq_conn.commit()

    def get_all_chat_ids(self) -> Set[int]:
        with self.with_cursor() as c:
            c.execute('SELECT chat_id FROM users')
            return {row[0] for row in c.fetchall()}

    def get_admin_chat_ids(self) -> Set[int]:
        with self.with_cursor() as c:
            c.execute('SELECT chat_id FROM users WHERE is_admin')
            return {row[0] for row in c.fetchall()}

    def is_admin(self, chat_id: int) -> bool:
        with self.with_cursor() as c:
            c.execute('SELECT is_admin FROM users WHERE chat_id=?', (chat_id,))
            row = c.fetchone()
            if row:
                return bool(row[0])
            return False

    def set_is_admin(self, chat_id: int, is_admin: bool):
        with self.with_cursor() as c:
            c.execute('UPDATE users SET is_admin=? WHERE chat_id=?', (int(is_admin), chat_id))
            self.sq_conn.commit()

    def schedule_message_to_delete(self, msg_to_delete: MessageToDelete, ttl: int):
        with self.with_cursor() as c:
            date_modifier = f'{ttl} seconds'
            c.execute('INSERT INTO msgs_to_delete (chat_id, message_id, delete_at) '
                      'VALUES (?, ?, datetime("now", ?))',
                      (msg_to_delete.chat_id, msg_to_delete.message_id, date_modifier))
        self.sq_conn.commit()

    def get_scheduled_for_deleting(self, limit: int) -> List[MessageToDelete]:
        with self.with_cursor() as c:
            c.execute('SELECT chat_id, message_id FROM msgs_to_delete WHERE delete_at <= datetime("now") LIMIT ?',
                      (limit,))
            return [MessageToDelete(*row) for row in c.fetchall()]

    def forget_msgs_to_delete(self, msgs: Iterable[MessageToDelete]):
        with self.with_cursor() as c:
            c.executemany('DELETE FROM msgs_to_delete WHERE chat_id=? AND message_id=?',
                          [(m.chat_id, m.message_id) for m in msgs])
            self.sq_conn.commit()

    def save_unconfirmed_text(self, text: str) -> int:
        with self.with_cursor() as c:
            c.execute('INSERT INTO unconfirmed_texts (text) VALUES (?)', (text,))
            rowid = c.lastrowid
            self.sq_conn.commit()
            return rowid

    def save_confirmation_message_id(self, text_id: int, confirmation_message_id: int):
        with self.with_cursor() as c:
            c.execute('UPDATE unconfirmed_texts SET confirmation_message_id=? WHERE id=?',
                      (confirmation_message_id, text_id))
            self.sq_conn.commit()

    def load_unconfirmed_text(self, text_id: int) -> UnconfirmedText:
        with self.with_cursor() as c:
            c.execute('SELECT text, confirmation_message_id FROM unconfirmed_texts WHERE id=?', (text_id,))
            row = c.fetchone()
            if row:
                return UnconfirmedText(text_id, row[0], row[1])

    def delete_unconfirmed_text(self, text_id: int):
        with self.with_cursor() as c:
            c.execute('DELETE FROM unconfirmed_texts WHERE id=?', (text_id,))
            self.sq_conn.commit()

    def save_send_tasks(self, chat_ids: Iterable[int], text: str):
        with self.with_cursor() as c:
            c.executemany('INSERT INTO send_tasks (chat_id, text) VALUES (?, ?)',
                          ((chat_id, text) for chat_id in chat_ids))
            self.sq_conn.commit()

    def load_send_tasks(self, limit: int) -> List[SendTask]:
        with self.with_cursor() as c:
            c.execute('SELECT id, chat_id, text FROM send_tasks ORDER BY id LIMIT ?', (limit,))
            return [SendTask(*row) for row in c.fetchall()]

    def dismiss_send_tasks(self, ids: Iterable[int]):
        with self.with_cursor() as c:
            c.executemany('DELETE FROM send_tasks WHERE id = ?', ((id,) for id in ids))
            self.sq_conn.commit()
