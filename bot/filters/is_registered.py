from aiogram.filters import BaseFilter
from aiogram.types import Message

from bot.db.database import User


class IsNotRegistered(BaseFilter):
    async def __call__(self, message: Message, volunteer: User | None = None) -> bool:
        return volunteer is None


class IsRegistered(BaseFilter):
    async def __call__(self, message: Message, volunteer: User | None = None) -> bool:
        return volunteer is not None
