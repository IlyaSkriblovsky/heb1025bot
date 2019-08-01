from dataclasses import dataclass
from typing import Set, List

from telegram import Bot, Update
from telegram.ext import Dispatcher, CommandHandler

from bots.aspects.autodelete import AutoDeleteStorage
from bots.db import Database


@dataclass
class User:
    chat_id: int
    first_name: str
    last_name: str
    username: str
    is_admin: bool

    def as_string(self) -> str:
        username = None
        if self.username is not None:
            username = '@' + self.username
        parts = [x for x in [self.first_name, self.last_name, username] if x is not None]
        if parts:
            return ' '.join(parts)
        return f'<#{self.chat_id}>'


class UsersStorage:
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


class UsersBehavior:
    def __init__(self, users_storage: UsersStorage, auto_delete_storage: AutoDeleteStorage):
        self.users_storage = users_storage
        self.auto_delete_storage = auto_delete_storage

    def install(self, dispatcher: Dispatcher):
        dispatcher.add_handler(CommandHandler('is_admin', self.is_admin))
        dispatcher.add_handler(CommandHandler('take_admin', self.take_admin))
        dispatcher.add_handler(CommandHandler('drop_admin', self.drop_admin))
        dispatcher.add_handler(CommandHandler('list_users', self.list_users))

    def is_admin(self, _bot: Bot, update: Update):
        if self.users_storage.is_admin(update.effective_chat.id):
            reply = 'Вы администратор'
        else:
            reply = 'Вы НЕ администратор'
        self.auto_delete_storage.schedule(update.message.reply_text(reply))

    def take_admin(self, _bot: Bot, update: Update):
        if self.users_storage.get_admin_chat_ids():
            self.auto_delete_storage.schedule(update.message.reply_text('Администраторы уже назначены'))
            return
        self.users_storage.set_is_admin(update.effective_chat.id, True)
        self.auto_delete_storage.schedule(update.message.reply_text('Теперь вы администратор'))

    def drop_admin(self, _bot: Bot, update: Update):
        self.users_storage.set_is_admin(update.effective_chat.id, False)
        self.auto_delete_storage.schedule(update.message.reply_text('Теперь вы НЕ администратор'))

    def list_users(self, _bot: Bot, update: Update):
        if not self.users_storage.is_admin(update.effective_chat.id):
            self.auto_delete_storage.schedule(
                update.message.reply_text('Только администратор может видеть список пользователей'))
            return

        lines = []
        for no, user in enumerate(self.users_storage.get_all_users(), 1):
            lines.append(f'{no}. {user.as_string()}')
        self.auto_delete_storage.schedule(update.message.reply_text('\n'.join(lines)))
