from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from bot.config import config


class IsAdmin(BaseFilter):
    """Admin check for both messages and callback queries.

    config.ADMIN_IDS is parsed once at startup; this used to re-split the raw
    environment string and call int() on every incoming message.
    """

    async def __call__(self, telegram_event: Message | CallbackQuery) -> bool:
        # Deliberately not named `event`: aiogram builds a filter's kwargs as
        # `{k: data[k] for k in params if k in data}` and passes the event
        # positionally, so a parameter sharing a name with any middleware data
        # key would be supplied twice.
        if telegram_event.from_user is None:
            return False
        return telegram_event.from_user.id in config.ADMIN_IDS
