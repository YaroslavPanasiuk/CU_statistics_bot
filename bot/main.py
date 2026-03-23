# main.py
import asyncio
from aiogram import Bot, Dispatcher
from bot.config import config
from bot.lexicon import Lexicon
from bot.handlers import register_handlers
from bot.db import database
from aiogram.fsm.storage.memory import MemoryStorage
from bot.utils.spreadsheets import load_volunteer_list, import_stats_from_sheet
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from bot.utils.schedulers import send_weekly_reminder

async def main():
    print('Loading lexicon...')
    Lexicon.load_from_sheet()
    print('Initializing database...')
    await database.init_db()
    print('Synchronizing volunteers...')
    await database.sync_volunteers(load_volunteer_list())
    print('Importing statistics...')
    await import_stats_from_sheet()
    print('...')
    bot = Bot(token=config.BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    register_handlers(dp)
    scheduler = AsyncIOScheduler(timezone=config.TIMEZONE)
    scheduler.add_job(send_weekly_reminder, trigger='cron', day_of_week='sun', hour=17, minute=0, kwargs={'bot': bot, 'level':1})
    scheduler.add_job(send_weekly_reminder, trigger='cron', day_of_week='sun', hour=19, minute=0, kwargs={'bot': bot, 'level':2})
    scheduler.add_job(send_weekly_reminder, trigger='cron', day_of_week='sun', hour=21, minute=0, kwargs={'bot': bot, 'level':3})
    scheduler.add_job(import_stats_from_sheet, trigger='cron', hour=0, minute=0)
    dp["scheduler"] = scheduler

    scheduler.start()

    print('bot started')
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())