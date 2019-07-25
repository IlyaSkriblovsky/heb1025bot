from threading import RLock
from typing import Callable


class CursorContext:
    def __init__(self, lock: RLock, cursor_getter, commit: bool, committer: Callable):
        self.lock = lock
        self.cursor_getter = cursor_getter
        self.commit = commit
        self.committer = committer

    def __enter__(self):
        self.lock.acquire()
        self.cursor = self.cursor_getter()
        return self.cursor

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cursor.close()
        if self.commit:
            self.committer()
        self.lock.release()


class SerializedDB:
    def __init__(self, db_conn):
        self.lock = RLock()

        self.db_conn = db_conn

    def with_cursor(self, *, commit=False):
        return CursorContext(self.lock, self.db_conn.cursor, commit, self.db_conn.commit)

    def commit(self):
        self.lock.acquire()
        try:
            self.db_conn.commit()
        finally:
            self.lock.release()
