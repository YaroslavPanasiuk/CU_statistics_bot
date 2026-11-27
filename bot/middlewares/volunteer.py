from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from bot.db import database


class VolunteerMiddleware(BaseMiddleware):
    """Resolve the telegram user to a volunteer row once per update.

    Filters and handlers both need it, and each used to look it up itself:
    IsRegistered/IsNotRegistered on three routers plus several handlers meant
    four or five identical SELECTs for a single message.

    `volunteer` is None for anyone not bound to a volunteer row, which is what
    IsRegistered and IsNotRegistered now test.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = data.get("event_from_user")
        data["volunteer"] = (
            await database.get_user_by_tg_id(tg_user.id) if tg_user is not None else None
        )
        return await handler(event, data)
