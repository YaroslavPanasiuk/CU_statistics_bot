import asyncio
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from bot.db import database
from bot.lexicon import select_random_line
from bot.utils.keyboards import get_main_menu_keyboard
from bot.utils.spreadsheets import fetch_users_with_no_stats
from datetime import datetime
from bot.config import config

async def send_weekly_reminder(bot: Bot, level: int, user_ids=None):
    if user_ids is None:
        current_week = datetime.now().isocalendar()[1]
        user_names = fetch_users_with_no_stats(current_week)
        users = [await database.get_user_by_full_name(user_name) for user_name in user_names]
        user_ids = [user.tg_id for user in users]
    for tg_id in user_ids:
        try:
            user = await database.get_user_by_tg_id(tg_id)
            await bot.send_message(
                tg_id,
                select_random_line(f"REMINDER{level}").format(user.full_name.split()[-1]), 
                reply_markup=get_main_menu_keyboard(),
                parse_mode="HTML"
            )
            await asyncio.sleep(0.5)
            await bot.send_message(config.ADMIN_IDS.split(',')[0], f"Reminded {user.full_name}, {level}")
            await asyncio.sleep(0.5)
        except TelegramForbiddenError:
            print(f"User {tg_id} blocked the bot.")
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            await bot.send_message(tg_id, "...") 
        except Exception as e:
            print(f"Failed to send to {tg_id}: {e}")