#!/usr/bin/env python
import json
import logging
import os
import sqlite3

from telegram import Update, Bot, Message, InlineKeyboardMarkup, InlineKeyboardButton, ParseMode
from telegram.error import BadRequest
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, Job, CallbackQueryHandler

from bots.db import SerializedDB
from bots.storage.autodelete import AutoDeleteStorage
from bots.storage.heb1025_users import Heb1025UsersStorage, User
from bots.storage.send_tasks import SendTasksStorage
from bots.storage.unconfirmed_texts import UnconfirmedTextsStorage
from bots.utils.plural import plural_ru

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)

logger = logging.getLogger(__name__)

db_conn = SerializedDB(sqlite3.connect('./heb1025.sqlite3', check_same_thread=False))
auto_delete_storage = AutoDeleteStorage(db_conn, 2 * 60 * 60)
users_storage = Heb1025UsersStorage(db_conn)
send_tasks_storage = SendTasksStorage(db_conn)
unconfirmed_texts_storage = UnconfirmedTextsStorage(db_conn)

PURGE_INTERVAL = 60
SEND_TASKS_INTERVAL = 2


def start(bot: Bot, update: Update):
    logger.info('start %s %s', update, type(update))
    users_storage.add_user(update.effective_chat.id, update.effective_user.first_name,
                           update.effective_user.last_name, update.effective_user.username)
    update.message.reply_text('Привет! Я буду присылать вам полезные сообщения время от времени')


def ping(bot: Bot, update: Update):
    auto_delete_storage.schedule(update.message.reply_text('pong'))


def on_text(bot: Bot, update: Update):
    if not users_storage.is_admin(update.effective_chat.id):
        auto_delete_storage.schedule(update.message.reply_text('Только администратор может рассылать сообщения'))
        return

    text = update.effective_message.text
    text_id = unconfirmed_texts_storage.save_text(text)

    confirmation_message: Message = update.message.reply_text(
        f'Подтвердите рассылку\n\n_{text}_', parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton('❌ Отмена', callback_data=json.dumps({'type': 'cancel', 'text_id': text_id})),
             InlineKeyboardButton('✅ Отправить', callback_data=json.dumps({'type': 'send', 'text_id': text_id}))]
        ])
    )
    auto_delete_storage.schedule(confirmation_message)
    unconfirmed_texts_storage.save_confirmation_message_id(text_id, confirmation_message.message_id)


def on_callback(bot: Bot, update: Update):
    callback_data = json.loads(update.callback_query.data)
    callback_type = callback_data['type']

    if callback_type == 'cancel':
        text_info = unconfirmed_texts_storage.load(callback_data['text_id'])
        bot.answer_callback_query(update.callback_query.id)
        unconfirmed_texts_storage.delete(text_info.id)
        bot.edit_message_text('❌ Рассылка отменена', update.effective_chat.id, text_info.confirmation_message_id)

    elif callback_type == 'send':
        text_info = unconfirmed_texts_storage.load(callback_data['text_id'])
        if text_info is None:
            auto_delete_storage.schedule(bot.send_message(update.effective_chat.id, 'Сообщение уже разослано'))
            return

        chat_ids = users_storage.get_all_chat_ids()
        n_users = len(chat_ids)
        send_tasks_storage.save(chat_ids, text_info.text)

        n_users_str = f'{n_users} ' + plural_ru(n_users, 'пользователю', 'пользователям', 'пользователям')
        send_tasks_storage.save({update.effective_chat.id}, f'✅ Отправлено {n_users_str}')

        unconfirmed_texts_storage.delete(text_info.id)
        bot.answer_callback_query(update.callback_query.id)
        bot.edit_message_text(f'⌛ Отправка {n_users_str}...', update.effective_chat.id,
                              text_info.confirmation_message_id)


def is_admin(bot: Bot, update: Update):
    if users_storage.is_admin(update.effective_chat.id):
        reply = 'Вы администратор'
    else:
        reply = 'Вы НЕ администратор'
    auto_delete_storage.schedule(update.message.reply_text(reply))


def take_admin(bot: Bot, update: Update):
    if users_storage.get_admin_chat_ids():
        auto_delete_storage.schedule(update.message.reply_text('Администраторы уже назначены'))
        return
    users_storage.set_is_admin(update.effective_chat.id, True)
    auto_delete_storage.schedule(update.message.reply_text('Теперь вы администратор'))


def drop_admin(bot: Bot, update: Update):
    users_storage.set_is_admin(update.effective_chat.id, False)
    auto_delete_storage.schedule(update.message.reply_text('Теперь вы НЕ администратор'))


def format_user(user: User) -> str:
    username = None
    if user.username is not None:
        username = '@' + user.username
    parts = [x for x in [user.first_name, user.last_name, username] if x is not None]
    if parts:
        return ' '.join(parts)
    return f'<#{user.chat_id}>'

def list_users(bot: Bot, update: Update):
    lines = []
    for no, user in enumerate(users_storage.get_all_users(), 1):
        lines.append(f'{no}. {format_user(user)}')
    update.message.reply_text('\n'.join(lines))

def schedule_remove(bot: Bot, update: Update):
    auto_delete_storage.schedule(update.message)


def error(bot, update, error):
    logger.warning('Update "%s" caused error "%s"', update, error)


def remove_scheduled(bot: Bot, job: Job):
    msgs = auto_delete_storage.get_scheduled(25)
    for msg in msgs:
        try:
            bot.delete_message(msg.chat_id, msg.message_id)
        except BadRequest as e:
            if e.message not in {'Message to delete not found', "Message can't be deleted"}:
                raise
    auto_delete_storage.forget(msgs)


def send_tasks(bot: Bot, job: Job):
    tasks = send_tasks_storage.load(20)
    for task in tasks:
        try:
            auto_delete_storage.schedule(bot.send_message(task.chat_id, task.text))
        except BadRequest as e:
            logger.exception('Error while sending message')
            pass
    send_tasks_storage.dismiss(task.task_id for task in tasks)


OBSERVE_FOR_REMOVE = 1

updater = Updater(os.environ['BOT_TOKEN'])

dp = updater.dispatcher

dp.add_handler(CommandHandler('start', start))
dp.add_handler(CommandHandler('ping', ping))
dp.add_handler(CommandHandler('is_admin', is_admin))
dp.add_handler(CommandHandler('take_admin', take_admin))
dp.add_handler(CommandHandler('drop_admin', drop_admin))
dp.add_handler(CommandHandler('list_users', list_users))

dp.add_handler(MessageHandler(Filters.text, on_text))
dp.add_handler(CallbackQueryHandler(on_callback))

dp.add_handler(MessageHandler(Filters.all, schedule_remove), group=OBSERVE_FOR_REMOVE)

updater.job_queue.run_repeating(remove_scheduled, PURGE_INTERVAL)
updater.job_queue.run_repeating(send_tasks, SEND_TASKS_INTERVAL)

dp.add_error_handler(error)

updater.start_polling()

updater.idle()
