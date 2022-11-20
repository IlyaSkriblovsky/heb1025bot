#!/usr/bin/env python
import json
import logging
import os
import sqlite3

from telegram import Update, Bot, Message, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.error import BadRequest, Unauthorized
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    Job,
    CallbackQueryHandler,
)

from bots.aspects.autodelete import (
    AutoDeleteStorage,
    install_autodelete,
    remove_scheduled,
    GROUP__OBSERVE_FOR_REMOVE,
)
from bots.aspects.send_tasks import SendTasksStorage
from bots.aspects.unconfirmed_texts import UnconfirmedTextsStorage
from bots.aspects.users import UsersStorage, UsersBehavior
from bots.db import SerializedDB
from bots.utils.bot_utils import create_ping
from bots.utils.plural import plural_ru


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

logger = logging.getLogger(__name__)

db_conn = SerializedDB(sqlite3.connect(os.environ["DB_FILE"], check_same_thread=False))
auto_delete_storage = AutoDeleteStorage(db_conn, 3 * 60 * 60)
users_storage = UsersStorage(db_conn, require_activation=True)
send_tasks_storage = SendTasksStorage(db_conn)
unconfirmed_texts_storage = UnconfirmedTextsStorage(db_conn)

SEND_TASKS_INTERVAL = 2


def start(bot: Bot, update: Update):
    users_storage.add_user(
        update.effective_chat.id,
        update.effective_user.first_name,
        update.effective_user.last_name,
        update.effective_user.username,
    )
    update.message.reply_text(
        "Привет! Я буду присылать вам полезные сообщения время от времени"
    )
    users_behavior.ask_for_activation(bot, update.message)


def on_text(bot: Bot, update: Update):
    if not users_storage.is_admin(update.effective_chat.id):
        auto_delete_storage.schedule(
            update.message.reply_text("Только администратор может рассылать сообщения")
        )
        return

    text = update.effective_message.text
    text_id = unconfirmed_texts_storage.save_text(text)

    confirmation_message: Message = update.message.reply_text(
        f"Подтвердите рассылку\n\n{text}",  # parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "❌ Отмена",
                        callback_data=json.dumps(
                            {"type": "cancel", "text_id": text_id}
                        ),
                    ),
                    InlineKeyboardButton(
                        "✅ Отправить",
                        callback_data=json.dumps({"type": "send", "text_id": text_id}),
                    ),
                ]
            ]
        ),
    )
    auto_delete_storage.schedule(confirmation_message)
    unconfirmed_texts_storage.save_confirmation_message_id(
        text_id, confirmation_message.message_id
    )


def on_clear_history(bot: Bot, update: Update):
    auto_delete_storage.schedule(update.message)
    if not users_storage.is_admin(update.effective_chat.id):
        auto_delete_storage.schedule(
            update.message.reply_text(
                "Только администратор может принудительно очистить историю"
            )
        )
        return

    reply: Message = update.message.reply_text(
        "✅ Все предыдущие сообщения у всех пользователей будут удалены в течение нескольких минут"
    )
    auto_delete_storage.schedule(reply)

    send_tasks_storage.dismiss_all()
    auto_delete_storage.reschedule_all_to_the_past()
    remove_scheduled(bot, auto_delete_storage)


def on_callback(bot: Bot, update: Update):
    callback_data = json.loads(update.callback_query.data)
    callback_type = callback_data["type"]

    if callback_type == "cancel":
        text_info = unconfirmed_texts_storage.load(callback_data["text_id"])
        bot.answer_callback_query(update.callback_query.id)
        unconfirmed_texts_storage.delete(text_info.id)
        bot.edit_message_text(
            "❌ Рассылка отменена",
            update.effective_chat.id,
            text_info.confirmation_message_id,
        )

    elif callback_type == "send":
        text_info = unconfirmed_texts_storage.load(callback_data["text_id"])
        if text_info is None:
            auto_delete_storage.schedule(
                bot.send_message(update.effective_chat.id, "Сообщение уже разослано")
            )
            return

        chat_ids = users_storage.get_all_chat_ids()
        n_users = len(chat_ids)
        send_tasks_storage.save(chat_ids, text_info.text)

        n_users_str = f"{n_users} " + plural_ru(
            n_users, "пользователю", "пользователям", "пользователям"
        )
        send_tasks_storage.save(
            {update.effective_chat.id}, f"✅ Отправлено {n_users_str}"
        )

        unconfirmed_texts_storage.delete(text_info.id)
        bot.answer_callback_query(update.callback_query.id)
        bot.edit_message_text(
            f"⌛ Отправка {n_users_str}...",
            update.effective_chat.id,
            text_info.confirmation_message_id,
        )


def error(bot, update, error):
    logger.warning('Update "%s" caused error "%s"', update, error)


def send_tasks(bot: Bot, job: Job):
    tasks = send_tasks_storage.load(20)
    for task in tasks:
        try:
            auto_delete_storage.schedule(bot.send_message(task.chat_id, task.text))
        except (BadRequest, Unauthorized) as e:
            logger.exception(f"Error while sending message to {task.chat_id}")
            pass
    send_tasks_storage.dismiss(task.task_id for task in tasks)


updater = Updater(os.environ["BOT_TOKEN"])

dp = updater.dispatcher

dp.add_handler(CommandHandler("start", start))
dp.add_handler(CommandHandler("ping", create_ping(auto_delete_storage)))
users_behavior = UsersBehavior(
    users_storage, auto_delete_storage, admin_requests_ttl=3 * 60 * 60
)
users_behavior.install(dp)

dp.add_handler(MessageHandler(Filters.text, on_text))
dp.add_handler(CallbackQueryHandler(on_callback))

dp.add_handler(
    CommandHandler("clear_history", on_clear_history), group=GROUP__OBSERVE_FOR_REMOVE
)
install_autodelete(dp, updater, auto_delete_storage)

updater.job_queue.run_repeating(send_tasks, SEND_TASKS_INTERVAL)

dp.add_error_handler(error)

updater.start_polling()

updater.idle()
