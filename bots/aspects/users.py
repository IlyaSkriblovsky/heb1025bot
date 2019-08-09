import json
from dataclasses import dataclass
from typing import Set, List, Iterable, Tuple, Optional

from telegram import Bot, Update, InlineKeyboardMarkup, InlineKeyboardButton, ParseMode
from telegram.ext import Dispatcher, CommandHandler, CallbackQueryHandler
from telegram.utils.helpers import escape_markdown

from bots.aspects.autodelete import AutoDeleteStorage
from bots.aspects.common import ChatAndMessageId
from bots.db import Database


@dataclass
class User:
    chat_id: int
    first_name: str
    last_name: str
    username: str
    is_admin: bool
    banned: bool

    def tg_link(self) -> str:
        return f'tg://user?id={self.chat_id}'

    def full_name(self) -> str:
        full_name = self.first_name
        if self.last_name:
            full_name += ' ' + self.last_name
        return full_name

    def as_markdown_link(self) -> str:
        return f'[{escape_markdown(self.full_name())}]({self.tg_link()})'


@dataclass
class AdminRequest:
    id: int
    candidate_chat_id: int
    request_text: str


@dataclass
class AdminRequestConfirmation:
    id: int
    request_id: int
    existing_admin_chat_id: int
    message_id: int


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
                    is_admin INTEGER DEFAULT 0,
                    banned INTEGER DEFAULT 0
                )
            ''')
            c.execute('''
                CREATE TABLE IF NOT EXISTS admin_requests (
                    id INTEGER PRIMARY KEY,
                    candidate_chat_id INTEGER,
                    request_text TEXT
                )
            ''')
            c.execute('''
                CREATE TABLE IF NOT EXISTS admin_request_confirmations (
                    id INTEGER PRIMARY KEY,
                    request_id INTEGER,
                    existing_admin_chat_id INTEGER,
                    message_id INTEGER
                )
            ''')

    def add_user(self, chat_id: int, first_name: str, last_name: str, username: str):
        with self.db.with_cursor(commit=True) as c:
            c.execute('SELECT chat_id FROM users WHERE chat_id=?', (chat_id,))
            row = c.fetchone()
            if row is None:
                c.execute(
                    'INSERT INTO users (chat_id, first_name, last_name, username, start_time)'
                    ' VALUES (?, ?, ?, ?, datetime("now"))',
                    (chat_id, first_name, last_name, username)
                )
            else:
                c.execute(
                    'UPDATE users SET first_name=?, last_name=?, username=?'
                    ' WHERE chat_id=?',
                    (first_name, last_name, username, chat_id)
                )

    def get_all_chat_ids(self) -> Set[int]:
        with self.db.with_cursor() as c:
            return {row[0] for row in c.execute('SELECT chat_id FROM users WHERE NOT banned')}

    def get_admin_chat_ids(self) -> Set[int]:
        with self.db.with_cursor() as c:
            return {row[0] for row in c.execute('SELECT chat_id FROM users WHERE NOT banned AND is_admin')}

    def is_admin(self, chat_id: int) -> bool:
        with self.db.with_cursor() as c:
            c.execute('SELECT is_admin FROM users WHERE chat_id=? AND NOT banned', (chat_id,))
            row = c.fetchone()
            if row:
                return bool(row[0])
            return False

    @staticmethod
    def _set_is_admin_with_cursor(cursor, chat_id: int, is_admin: bool):
        cursor.execute('UPDATE users SET is_admin=? WHERE chat_id=? AND NOT banned', (int(is_admin), chat_id))

    def set_is_admin(self, chat_id: int, is_admin: bool):
        with self.db.with_cursor(commit=True) as c:
            return self._set_is_admin_with_cursor(c, chat_id, is_admin)

    def is_banned(self, chat_id: int) -> bool:
        with self.db.with_cursor() as c:
            c.execute('SELECT banned FROM users WHERE chat_id=?', (chat_id,))
            row = c.fetchone()
            if row:
                return bool(row[0])
            return True

    def set_banned(self, chat_id: int, banned: bool):
        with self.db.with_cursor(commit=True) as c:
            c.execute('UPDATE users SET banned=? WHERE chat_id=?', (int(banned), chat_id))

    @staticmethod
    def _get_users(cursor, where: Optional[str], where_args: tuple = ()) -> List[User]:
        full_where = f'WHERE {where}' if where else ''
        return [
            User(*row[:4], bool(row[4]), bool(row[5]))
            for row in cursor.execute(f'''
                SELECT chat_id, first_name, last_name, username, is_admin, banned
                FROM users
                {full_where}
                ORDER BY start_time
            ''', where_args)
        ]

    def get_all_users(self, include_banned=False) -> List[User]:
        with self.db.with_cursor() as c:
            return self._get_users(c, None if include_banned else 'NOT banned')

    def get_banned_users(self) -> List[User]:
        with self.db.with_cursor() as c:
            return self._get_users(c, 'banned')

    def get_user(self, chat_id: int) -> Optional[User]:
        with self.db.with_cursor() as c:
            users = self._get_users(c, 'chat_id=?', (chat_id,))
            if users:
                return users[0]

    def create_admin_request(self, candidate_chat_id: int, request_text: str) -> AdminRequest:
        with self.db.with_cursor(commit=True) as c:
            c.execute('INSERT INTO admin_requests (candidate_chat_id, request_text) VALUES (?, ?)',
                      (candidate_chat_id, request_text))
            return AdminRequest(c.lastrowid, candidate_chat_id, request_text)

    def save_admin_request_confirmations(self, request_id: int, confirmations: Iterable[ChatAndMessageId]):
        with self.db.with_cursor(commit=True) as c:
            c.executemany(
                'INSERT INTO admin_request_confirmations (request_id, existing_admin_chat_id, message_id)'
                ' values (?, ?, ?)',
                ((request_id, m.chat_id, m.message_id) for m in confirmations)
            )

    @staticmethod
    def _clear_admin_request(cursor, request_id: int) -> Tuple[AdminRequest, Iterable[ChatAndMessageId]]:
        cursor.execute('SELECT candidate_chat_id, request_text FROM admin_requests WHERE id=?', (request_id,))
        candidate_chat_id, request_text = cursor.fetchone()
        confirmations = [
            ChatAndMessageId(*row)
            for row in cursor.execute('SELECT existing_admin_chat_id, message_id FROM admin_request_confirmations'
                                      ' WHERE request_id=?', (request_id,))
        ]
        cursor.execute('DELETE FROM admin_request_confirmations WHERE request_id=?', (request_id,))
        cursor.execute('DELETE FROM admin_requests WHERE id=?', (request_id,))
        return AdminRequest(request_id, candidate_chat_id, request_text), confirmations

    def reject_admin_request(self, request_id: int) -> Tuple[AdminRequest, Iterable[ChatAndMessageId]]:
        with self.db.with_cursor(commit=True) as c:
            return self._clear_admin_request(c, request_id)

    def accept_admin_request(self, request_id: int) -> Tuple[AdminRequest, Iterable[ChatAndMessageId]]:
        with self.db.with_cursor(commit=True) as c:
            request, confirmations = self._clear_admin_request(c, request_id)
            self._set_is_admin_with_cursor(c, request.candidate_chat_id, True)
            return request, confirmations


GROUP__USERS = 2


def j(data):
    return json.dumps(data)


class UsersBehavior:
    def __init__(self, users_storage: UsersStorage, auto_delete_storage: AutoDeleteStorage, admin_requests_ttl: int,
                 admin_greeting: str = 'Теперь вы администратор'):
        self.users_storage = users_storage
        self.auto_delete_storage = auto_delete_storage
        self.admin_requests_ttl = admin_requests_ttl
        self.admin_greeting = admin_greeting

    def install(self, dispatcher: Dispatcher):
        dispatcher.add_handler(CommandHandler('is_admin', self.is_admin), group=GROUP__USERS)
        dispatcher.add_handler(CommandHandler('take_admin', self.take_admin), group=GROUP__USERS)
        dispatcher.add_handler(CommandHandler('drop_admin', self.drop_admin), group=GROUP__USERS)
        dispatcher.add_handler(CommandHandler('list_users', self.list_users), group=GROUP__USERS)
        dispatcher.add_handler(CommandHandler('ban_list', self.ban_list), group=GROUP__USERS)
        dispatcher.add_handler(CommandHandler('ban', self.ban_user, pass_args=True), group=GROUP__USERS)
        dispatcher.add_handler(CommandHandler('unban', self.unban_user, pass_args=True), group=GROUP__USERS)
        dispatcher.add_handler(CallbackQueryHandler(self.on_callback), group=GROUP__USERS)

    def is_admin(self, _bot: Bot, update: Update):
        if self.users_storage.is_admin(update.effective_chat.id):
            reply = 'Вы администратор'
        else:
            reply = 'Вы НЕ администратор'
        self.auto_delete_storage.schedule(update.message.reply_text(reply))

    def take_admin(self, bot: Bot, update: Update):
        if self.users_storage.is_banned(update.effective_chat.id):
            self.auto_delete_storage.schedule(update.message.reply_text('Вы забанены'))
            return

        existing_admins = self.users_storage.get_admin_chat_ids()
        if not existing_admins:
            self.users_storage.set_is_admin(update.effective_chat.id, True)
            self.auto_delete_storage.schedule(
                update.message.reply_text(self.admin_greeting, parse_mode=ParseMode.MARKDOWN),
                ttl=self.admin_requests_ttl
            )
        elif update.effective_chat.id in existing_admins:
            self.auto_delete_storage.schedule(update.message.reply_text('Вы уже администратор'))
        else:
            request_text = f'{update.effective_user.mention_markdown()} хочет стать администратором'
            request = self.users_storage.create_admin_request(update.effective_chat.id, request_text)

            confirmation_msgs = []
            for chat_id in existing_admins:
                confirmation_msg = bot.send_message(
                    chat_id, request_text,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton('❌ Отклонить', callback_data=json.dumps(
                            {'type': 'reject_admin_request', 'request_id': request.id})),
                         InlineKeyboardButton('✅ Принять', callback_data=json.dumps(
                             {'type': 'accept_admin_request', 'request_id': request.id}))]
                    ])
                )
                self.auto_delete_storage.schedule(confirmation_msg, ttl=self.admin_requests_ttl)
                confirmation_msgs.append(ChatAndMessageId(chat_id, confirmation_msg.message_id))
            self.users_storage.save_admin_request_confirmations(request.id, confirmation_msgs)

            self.auto_delete_storage.schedule(update.message.reply_text(
                'Заявка принята. Её должен одобрить один из текущих администраторов.'
            ))

    def on_callback(self, bot: Bot, update: Update):
        callback_data = json.loads(update.callback_query.data)
        callback_type = callback_data['type']

        if callback_type == 'reject_admin_request':
            bot.answer_callback_query(update.callback_query.id)
            request_id = callback_data['request_id']
            request, confirmations = self.users_storage.reject_admin_request(request_id)
            self.auto_delete_storage.schedule(bot.send_message(request.candidate_chat_id, '❌ Ваша заявка отклонена'))
            for msg in confirmations:
                bot.edit_message_text(
                    request.request_text + f'\n\n❌ Отклонено администратором {update.effective_user.mention_markdown()}',
                    msg.chat_id, msg.message_id,
                    parse_mode=ParseMode.MARKDOWN
                )
                self.auto_delete_storage.schedule_by_ids(msg)

        elif callback_type == 'accept_admin_request':
            bot.answer_callback_query(update.callback_query.id)
            request_id = callback_data['request_id']
            request, confirmations = self.users_storage.accept_admin_request(request_id)
            self.auto_delete_storage.schedule(
                bot.send_message(request.candidate_chat_id, self.admin_greeting, parse_mode=ParseMode.MARKDOWN),
                ttl=self.admin_requests_ttl
            )
            for msg in confirmations:
                bot.edit_message_text(
                    request.request_text + f'\n\n✅ Принято администратором {update.effective_user.mention_markdown()}',
                    msg.chat_id, msg.message_id,
                    parse_mode=ParseMode.MARKDOWN
                )
                self.auto_delete_storage.schedule_by_ids(msg)

        elif callback_type in {'unban_cancel', 'ban_cancel'}:
            bot.edit_message_text('❌ Команда отменена', update.effective_chat.id, update.effective_message.message_id)
            bot.answer_callback_query(update.callback_query.id)

        elif callback_type in {'ban', 'unban'}:
            chat_id = callback_data['chat_id']
            user = self.users_storage.get_user(chat_id)
            self.users_storage.set_banned(chat_id, callback_type == 'ban')
            action = 'забанен' if callback_type == 'ban' else 'разбанен'
            icon = '\U0001f6ab' if callback_type == 'ban' else '\U0001f513'
            bot.edit_message_text(f'{icon} Пользователь {user.as_markdown_link()} {action}',
                                  parse_mode=ParseMode.MARKDOWN,
                                  chat_id=update.effective_chat.id, message_id=update.effective_message.message_id)
            bot.answer_callback_query(update.callback_query.id)

    def drop_admin(self, _bot: Bot, update: Update):
        self.users_storage.set_is_admin(update.effective_chat.id, False)
        self.auto_delete_storage.schedule(update.message.reply_text('Теперь вы НЕ администратор'))

    def _if_not_admin(self, update: Update, msg: str):
        if self.users_storage.is_admin(update.effective_chat.id):
            return False

        self.auto_delete_storage.schedule(update.message.reply_text(msg))
        return True

    def list_users(self, _bot: Bot, update: Update):
        if self._if_not_admin(update, 'Только администратор может видеть списки пользователей'):
            return

        admins = self.users_storage.get_admin_chat_ids()

        lines = []
        for no, user in enumerate(self.users_storage.get_all_users(), 1):
            suffix = ' \U0001f511' if user.chat_id in admins else ''
            lines.append(f'{no}. {user.as_markdown_link()}{suffix}')
        self.auto_delete_storage.schedule(update.message.reply_text(
            'Активные пользователи:\n\n' + '\n'.join(lines), parse_mode=ParseMode.MARKDOWN
        ))

    def ban_list(self, _bot: Bot, update: Update):
        if self._if_not_admin(update, 'Только администратор может видеть списки пользователей'):
            return

        users = self.users_storage.get_banned_users()

        self.auto_delete_storage.schedule(update.message.reply_text(
            'Заблокированные пользователи:\n\n'
            + '\n'.join(f'{no}. {user.as_markdown_link()}' for no, user in enumerate(users, 1)),
            parse_mode=ParseMode.MARKDOWN))

    def ban_user(self, _bot: Bot, update: Update, args: List[str]):
        if self._if_not_admin(update, 'Только администратор может управлять пользователями'):
            return

        if not args:
            self.auto_delete_storage.schedule(update.message.reply_text(
                'Для блокировки пользователя наберите /list\_users, а затем `/ban N`,'
                ' где N — номер пользователя в списке',
                parse_mode=ParseMode.MARKDOWN
            ))
            return

        try:
            number = int(args[0])
        except ValueError:
            self.auto_delete_storage.schedule(update.message.reply_text(
                'Некоректный номер пользователя'
            ))
            return

        users = self.users_storage.get_all_users()
        if not 1 <= number <= len(users):
            self.auto_delete_storage.schedule(update.message.reply_text(
                'Некоректный номер пользователя'
            ))
            return

        user = users[number - 1]
        self.auto_delete_storage.schedule(update.message.reply_text(
            f'❓ Подтвердите блокировку пользователя {user.as_markdown_link()}',
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton('❌ Отмена', callback_data=j({'type': 'ban_cancel'})),
                 InlineKeyboardButton('\U0001f6ab Заблокировать', callback_data=j({'type': 'ban', 'chat_id': user.chat_id}))]
            ])
        ))

    def unban_user(self, _bot: Bot, update: Update, args: List[str]):
        if self._if_not_admin(update, 'Только администратор может управлять пользователями'):
            return

        if not args:
            self.auto_delete_storage.schedule(update.message.reply_text(
                'Для разблокировки пользователя наберите /ban\_list, а затем `/unban N`,'
                ' где N — номер заблокированного пользователя в списке',
                parse_mode=ParseMode.MARKDOWN
            ))
            return

        try:
            number = int(args[0])
        except ValueError:
            self.auto_delete_storage.schedule(update.message.reply_text(
                'Некоректный номер пользователя'
            ))
            return

        banned = self.users_storage.get_banned_users()
        if not 1 <= number <= len(banned):
            self.auto_delete_storage.schedule(update.message.reply_text(
                'Некоректный номер пользователя'
            ))
            return

        user = banned[number - 1]

        self.auto_delete_storage.schedule(update.message.reply_text(
            f'❓ Подтвердите разблокировку пользователя {user.as_markdown_link()}',
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton('❌ Отмена', callback_data=j({'type': 'unban_cancel'})),
                 InlineKeyboardButton('\U0001f513 Разблокировать', callback_data=j({'type': 'unban', 'chat_id': user.chat_id}))]
            ])
        ))
