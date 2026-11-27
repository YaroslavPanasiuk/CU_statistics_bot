# main.py
import asyncio
from aiogram import Bot, Dispatcher
from bot.config import config
from bot.lexicon import Lexicon, validate_config
from bot.handlers import register_handlers
from bot.db import database
from aiogram.fsm.storage.memory import MemoryStorage
from bot.utils.spreadsheets import load_volunteer_list, import_stats_from_sheet
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from bot.utils.schedulers import send_weekly_reminder

async def main():
    print('Loading lexicon...')
    Lexicon.load_from_sheet()
    # Fail here, with a specific message, rather than in a handler hours later.
    validate_config()
    print('Initializing database...')
    await database.init_db()

    print('Synchronizing volunteers...')
    try:
        await database.sync_volunteers(await load_volunteer_list())
    except Exception as e:
        # A Sheets outage must not take the bot offline. load_volunteer_list
        # raises rather than returning an empty roster -- which sync_volunteers
        # would read as "delete every volunteer" -- so carry on with whatever
        # roster the database already holds. With Restart=always, raising here
        # would mean a restart loop every 5 seconds.
        print(f"Volunteer sync skipped, continuing with the stored roster: {e}")

    print('Importing statistics...')
    await import_stats_from_sheet()

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
    try:
        await dp.start_polling(bot)
    finally:
        # systemd sends SIGTERM on restart. Close everything so Telegram
        # releases the long poll instead of holding it for several seconds,
        # and so Postgres connections are returned rather than dropped.
        print('shutting down...')
        scheduler.shutdown(wait=False)
        await bot.session.close()
        await database.dispose_db()

if __name__ == "__main__":
    asyncio.run(main())
