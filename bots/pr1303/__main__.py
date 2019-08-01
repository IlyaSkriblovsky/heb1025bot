import json
import logging
import os
import sqlite3

from telegram import Bot, Update, ParseMode, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackQueryHandler

from bots.aspects.autodelete import (AutoDeleteStorage, install_all_inbound_messages_for_delete,
                                     install_remove_scheduled_job)
from bots.aspects.users import UsersStorage, UsersBehavior
from bots.db import SerializedDB
from bots.utils.bot_utils import create_ping

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)

logger = logging.getLogger(__name__)

db_conn = SerializedDB(sqlite3.connect('./pr1303.sqlite3', check_same_thread=False))
users_storage = UsersStorage(db_conn)
auto_delete_storage = AutoDeleteStorage(db_conn, 3) # FIXME 10 * 60)

ADMIN_MSG_TTL = 10  # 2 * 24 * 60 * 60


def start(bot: Bot, update: Update):
    users_storage.add_user(update.effective_chat.id, update.effective_user.first_name,
                           update.effective_user.last_name, update.effective_user.username)
    update.message.reply_text('Привет! Я могу пересылать кое-что кое-куда.')


def on_text(bot: Bot, update: Update):
    # text_to_send = update.effective_message.text

    link = ''
    if update.effective_user.username:
        link = f' @{update.effective_user.username}'
    text_to_send = f'_{update.effective_user.first_name} {update.effective_user.last_name}{link}_' \
                   f'\n\n{update.message.text}'

    for chat_id in users_storage.get_admin_chat_ids():
        auto_delete_storage.schedule(
            bot.send_message(
                chat_id, text_to_send, parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton('\U0001f5d1\ufe0f Удалить', callback_data=json.dumps({'type': 'delete'}))]
                ])
            ),
            ttl=ADMIN_MSG_TTL
        )
    auto_delete_storage.schedule(update.message.reply_text('✅ Отправлено'))


def on_callback(bot: Bot, update: Update):
    callback_data = json.loads(update.callback_query.data)
    callback_type = callback_data['type']

    if callback_type == 'delete':
        bot.answer_callback_query(update.callback_query.id)
        bot.delete_message(update.effective_chat.id, update.effective_message.message_id)


def error(bot, update, error):
    logger.warning('Update "%s" caused error "%s"', update, error)


updater = Updater(os.environ['BOT_TOKEN'])

dp = updater.dispatcher

dp.add_handler(CommandHandler('start', start))
dp.add_handler(CommandHandler('ping', create_ping(auto_delete_storage)))
UsersBehavior(users_storage, auto_delete_storage).install(dp)

dp.add_handler(MessageHandler(Filters.text, on_text))
dp.add_handler(CallbackQueryHandler(on_callback))

install_all_inbound_messages_for_delete(dp, auto_delete_storage)
install_remove_scheduled_job(updater, auto_delete_storage, interval=3)

dp.add_error_handler(error)

updater.start_polling()

updater.idle()
