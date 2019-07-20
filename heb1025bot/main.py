#!/usr/bin/env python
import json
import logging
import os

from telegram import Update, Bot, Message, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, Job, CallbackQueryHandler

from heb1025bot.plural import plural_ru
from heb1025bot.storage import Storage

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)

logger = logging.getLogger(__name__)

storage = Storage()

DEFAULT_TTL = 3 * 60
PURGE_INTERVAL = 1


def schedule_for_delete(msg: Message):
    storage.schedule_message_to_delete(msg.chat_id, msg.message_id, DEFAULT_TTL)


def start(bot: Bot, update: Update):
    logger.info('start %s %s', update, type(update))
    storage.add_user(update.effective_chat.id, update.effective_user.first_name,
                     update.effective_user.last_name, update.effective_user.username)
    update.message.reply_text('Привет! Я буду присылать вам полезные сообщения время от времени')


def ping(bot: Bot, update: Update):
    schedule_for_delete(update.message.reply_text('pong'))


def on_text(bot: Bot, update: Update):
    if not storage.is_admin(update.effective_chat.id):
        schedule_for_delete(update.message.reply_text('Только администратор может рассылать сообщения'))
        return

    schedule_for_delete(update.message.reply_text(
        f'Подтвердите рассылку «{update.effective_message.text}»',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton('❌ Отмена', callback_data=json.dumps({'type': 'cancel'})),
             InlineKeyboardButton('✅ Отправить',
                                  callback_data=json.dumps({'type': 'send', 'text': update.effective_message.text}))]
        ])
    ))


def on_callback(bot: Bot, update: Update):
    callback_data = json.loads(update.callback_query.data)
    callback_type = callback_data['type']

    if callback_type == 'cancel':
        bot.answer_callback_query(update.callback_query.id)
        schedule_for_delete(bot.send_message(update.effective_chat.id, '❌ Рассылка отменена'))

    elif callback_type == 'send':
        chat_ids = storage.get_all_chat_ids()
        for chat_id in chat_ids:
            schedule_for_delete(bot.send_message(chat_id, callback_data['text']))
        bot.answer_callback_query(update.callback_query.id)
        schedule_for_delete(bot.send_message(
            update.effective_chat.id,
            f'✅ Отправлено {len(chat_ids)} {plural_ru(len(chat_ids), "пользователю", "пользователям", "пользователям")}'
        ))


def is_admin(bot: Bot, update: Update):
    if storage.is_admin(update.effective_chat.id):
        reply = 'Вы администратор'
    else:
        reply = 'Вы НЕ администратор'
    schedule_for_delete(update.message.reply_text(reply))


def take_admin(bot: Bot, update: Update):
    if storage.get_admin_chat_ids():
        schedule_for_delete(update.message.reply_text('Администраторы уже назначены'))
        return
    storage.set_is_admin(update.effective_chat.id, True)
    schedule_for_delete(update.message.reply_text('Теперь вы администратор'))


def drop_admin(bot: Bot, update: Update):
    storage.set_is_admin(update.effective_chat.id, False)
    schedule_for_delete(update.message.reply_text('Теперь вы НЕ администратор'))


def schedule_remove(bot: Bot, update: Update):
    schedule_for_delete(update.message)


def error(bot, update, error):
    logger.warning('Update "%s" caused error "%s"', update, error)


def remove_scheduled(bot: Bot, job: Job):
    scheduled = storage.get_scheduled_for_deleting()
    for chat_id, message_id in scheduled:
        bot.delete_message(chat_id, message_id)
    storage.dismiss_scheduled_messages(scheduled)


TOKEN = os.environ['BOT_TOKEN']

OBSERVE_FOR_REMOVE = 1

if __name__ == '__main__':
    updater = Updater(TOKEN)

    dp = updater.dispatcher

    dp.add_handler(CommandHandler('start', start))
    dp.add_handler(CommandHandler('ping', ping))
    # dp.add_handler(CommandHandler('say', say, pass_args=True))
    dp.add_handler(CommandHandler('is_admin', is_admin))
    dp.add_handler(CommandHandler('take_admin', take_admin))
    dp.add_handler(CommandHandler('drop_admin', drop_admin))

    dp.add_handler(MessageHandler(Filters.text, on_text))
    dp.add_handler(CallbackQueryHandler(on_callback))

    dp.add_handler(MessageHandler(Filters.all, schedule_remove), group=OBSERVE_FOR_REMOVE)

    updater.job_queue.run_repeating(remove_scheduled, PURGE_INTERVAL)

    dp.add_error_handler(error)

    updater.start_polling()

    updater.idle()
