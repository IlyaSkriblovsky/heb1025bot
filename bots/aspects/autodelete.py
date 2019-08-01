from dataclasses import dataclass
from typing import List, Iterable

from telegram import Message, Bot
from telegram.error import BadRequest
from telegram.ext import Dispatcher, MessageHandler, Filters, Updater

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


PURGE_INTERVAL = 60
GROUP__OBSERVE_FOR_REMOVE = 1


def remove_scheduled(bot: Bot, auto_delete_storage: AutoDeleteStorage):
    msgs = auto_delete_storage.get_scheduled(25)
    for msg in msgs:
        try:
            bot.delete_message(msg.chat_id, msg.message_id)
        except BadRequest as e:
            if e.message not in {'Message to delete not found', "Message can't be deleted"}:
                raise
    auto_delete_storage.forget(msgs)


def install_all_inbound_messages_for_delete(dispatcher: Dispatcher, auto_delete_storage: AutoDeleteStorage):
    dispatcher.add_handler(
        MessageHandler(Filters.all, lambda bot, update: auto_delete_storage.schedule(update.message)),
        group=GROUP__OBSERVE_FOR_REMOVE
    )


def install_remove_scheduled_job(updater: Updater, auto_delete_storage: AutoDeleteStorage,
                                 interval: int = PURGE_INTERVAL):
    updater.job_queue.run_repeating(lambda bot, job: remove_scheduled(bot, auto_delete_storage), interval)
