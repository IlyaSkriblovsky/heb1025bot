from telegram import Bot, Update

from bots.aspects.autodelete import AutoDeleteStorage


def create_ping(auto_delete_storage: AutoDeleteStorage):
    def ping(bot: Bot, update: Update):
        auto_delete_storage.schedule(update.message.reply_text('pong'))
    return ping
