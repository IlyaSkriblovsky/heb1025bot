#!/usr/bin/env python
import json
import logging
import os

from telegram import Update, Bot, Message, InlineKeyboardMarkup, InlineKeyboardButton, ParseMode
from telegram.error import BadRequest
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, Job, CallbackQueryHandler

from heb1025bot.plural import plural_ru
from heb1025bot.storage import Storage, MessageToDelete

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)

logger = logging.getLogger(__name__)

storage = Storage()

DEFAULT_TTL = 2 * 60 * 60
PURGE_INTERVAL = 60
SEND_TASKS_INTERVAL = 2


def schedule_for_delete(msg: Message):
    storage.schedule_message_to_delete(MessageToDelete(msg.chat_id, msg.message_id), DEFAULT_TTL)


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

    text = update.effective_message.text
    text_id = storage.save_unconfirmed_text(text)

    confirmation_message: Message = update.message.reply_text(
        f'Подтвердите рассылку\n\n_{text}_', parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton('❌ Отмена', callback_data=json.dumps({'type': 'cancel', 'text_id': text_id})),
             InlineKeyboardButton('✅ Отправить', callback_data=json.dumps({'type': 'send', 'text_id': text_id}))]
        ])
    )
    schedule_for_delete(confirmation_message)
    storage.save_confirmation_message_id(text_id, confirmation_message.message_id)


def on_callback(bot: Bot, update: Update):
    callback_data = json.loads(update.callback_query.data)
    callback_type = callback_data['type']

    if callback_type == 'cancel':
        text_info = storage.load_unconfirmed_text(callback_data['text_id'])
        bot.answer_callback_query(update.callback_query.id)
        storage.delete_unconfirmed_text(text_info.id)
        bot.edit_message_text('❌ Рассылка отменена', update.effective_chat.id, text_info.confirmation_message_id)

    elif callback_type == 'send':
        chat_ids = storage.get_all_chat_ids()
        text_id = callback_data['text_id']
        text_info = storage.load_unconfirmed_text(text_id)
        if text_info is None:
            schedule_for_delete(bot.send_message(update.effective_chat.id, 'Сообщение уже разослано'))
            return

        storage.save_send_tasks(chat_ids, text_info.text)

        n_users_str = f'{len(chat_ids)} ' + plural_ru(len(chat_ids), 'пользователю', 'пользователям', 'пользователям')
        storage.save_send_tasks({update.effective_chat.id}, f'✅ Отправлено {n_users_str}')

        storage.delete_unconfirmed_text(text_id)
        bot.answer_callback_query(update.callback_query.id)
        bot.edit_message_text(f'⌛ Отправка {n_users_str}...', update.effective_chat.id,
                              text_info.confirmation_message_id)


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
    msgs = storage.get_scheduled_for_deleting(25)
    for msg in msgs:
        try:
            bot.delete_message(msg.chat_id, msg.message_id)
        except BadRequest as e:
            if e.message != 'Message to delete not found':
                raise
    storage.forget_msgs_to_delete(msgs)


def send_tasks(bot: Bot, job: Job):
    tasks = storage.load_send_tasks(20)
    for task in tasks:
        schedule_for_delete(bot.send_message(task.chat_id, task.text))
    storage.dismiss_send_tasks(task.task_id for task in tasks)


OBSERVE_FOR_REMOVE = 1

if __name__ == '__main__':
    updater = Updater(os.environ['BOT_TOKEN'])

    dp = updater.dispatcher

    dp.add_handler(CommandHandler('start', start))
    dp.add_handler(CommandHandler('ping', ping))
    dp.add_handler(CommandHandler('is_admin', is_admin))
    dp.add_handler(CommandHandler('take_admin', take_admin))
    dp.add_handler(CommandHandler('drop_admin', drop_admin))

    dp.add_handler(MessageHandler(Filters.text, on_text))
    dp.add_handler(CallbackQueryHandler(on_callback))

    dp.add_handler(MessageHandler(Filters.all, schedule_remove), group=OBSERVE_FOR_REMOVE)

    updater.job_queue.run_repeating(remove_scheduled, PURGE_INTERVAL)
    updater.job_queue.run_repeating(send_tasks, SEND_TASKS_INTERVAL)

    dp.add_error_handler(error)

    updater.start_polling()

    updater.idle()
