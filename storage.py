import sqlite3
from threading import Lock
from typing import Set, Tuple, List


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
        self.sq_conn.commit()

    # def get_cursor(self):
    #     self.__initialize()
    #     return self.sq_conn.cursor()

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

    def schedule_message_to_delete(self, chat_id: int, message_id: int, ttl: int):
        with self.with_cursor() as c:
            date_modifier = f'{ttl} seconds'
            c.execute('INSERT INTO msgs_to_delete (chat_id, message_id, delete_at) '
                      'VALUES (?, ?, datetime("now", ?))', (chat_id, message_id, date_modifier))
        self.sq_conn.commit()

    def get_scheduled_for_deleting(self) -> List[Tuple[int, int]]:
        with self.with_cursor() as c:
            c.execute('SELECT chat_id, message_id FROM msgs_to_delete WHERE delete_at <= datetime("now")')
            return c.fetchall()

    def dismiss_scheduled_messages(self, msgs: List[Tuple[int, int]]):
        with self.with_cursor() as c:
            c.executemany('DELETE FROM msgs_to_delete WHERE chat_id=? AND message_id=?', msgs)
            self.sq_conn.commit()
