from aiogram.filters import BaseFilter
from aiogram.types import Message
from bot.db.database import get_user_by_tg_id

class IsNotRegistered(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        user = await get_user_by_tg_id(message.from_user.id)
        return user is None
