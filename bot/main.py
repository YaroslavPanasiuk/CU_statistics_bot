# main.py
import asyncio
from aiogram import Bot, Dispatcher
from bot.config import config
from bot.lexicon import Lexicon, select_random_line
from bot.handlers import register_handlers
from bot.db import database
from aiogram.fsm.storage.memory import MemoryStorage
from bot.utils.spreadsheets import load_volunteer_list
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from bot.utils.schedulers import send_weekly_reminder

async def main():
    Lexicon.load_from_sheet()
    await database.init_db()
    await database.sync_volunteers(load_volunteer_list())
    bot = Bot(token=config.BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    register_handlers(dp)

    scheduler = AsyncIOScheduler(timezone='Europe/Kyiv')
    
    scheduler.add_job(send_weekly_reminder, trigger='cron', day_of_week='sun', hour=17, minute=0, kwargs={'bot': bot, 'level':1})
    scheduler.add_job(send_weekly_reminder, trigger='cron', day_of_week='sun', hour=19, minute=0, kwargs={'bot': bot, 'level':2})
    scheduler.add_job(send_weekly_reminder, trigger='cron', day_of_week='sun', hour=21, minute=0, kwargs={'bot': bot, 'level':3})
    dp["scheduler"] = scheduler

    scheduler.start()

    print('bot started')
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())