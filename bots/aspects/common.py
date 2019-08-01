from dataclasses import dataclass


@dataclass
class ChatAndMessageId:
    chat_id: int
    message_id: int
