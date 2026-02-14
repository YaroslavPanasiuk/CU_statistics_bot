# filters/is_admin.py
from aiogram.filters import BaseFilter
from aiogram.types import Message
from bot.config import config
from bot.lexicon import Lexicon

class IsAdmin(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        admin_ids = config.ADMIN_IDS.split(',')
        for id in admin_ids:
            if message.from_user.id == int(id):
                return True
        return False