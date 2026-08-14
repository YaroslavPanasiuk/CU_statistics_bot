from aiogram.filters import BaseFilter
from aiogram.types import Message
from bot.config import config
from bot.lexicon import Lexicon
from datetime import datetime
from bot.utils.maths import check_week_in_range

class BotAvailable(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        return check_week_in_range()