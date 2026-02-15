import asyncio
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from bot.db import database
from bot.lexicon import select_random_line
from bot.utils.keyboards import get_main_menu_keyboard

async def send_weekly_reminder(bot: Bot, level: int, user_ids):
    for tg_id in user_ids:
        try:
            user = await database.get_user_by_tg_id(tg_id)
            await bot.send_message(
                tg_id,
                select_random_line(f"REMINDER{level}").format(user.full_name.split()[-1]), 
                reply_markup=get_main_menu_keyboard()
            )
            await asyncio.sleep(0.5)
        except TelegramForbiddenError:
            print(f"User {tg_id} blocked the bot.")
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            await bot.send_message(tg_id, "...") 
        except Exception as e:
            print(f"Failed to send to {tg_id}: {e}")