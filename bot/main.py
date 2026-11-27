# main.py
import asyncio
import logging
from aiogram import Bot, Dispatcher
from bot.config import config
from bot.logging_setup import configure_logging
from bot import lexicon
from bot.lexicon import Lexicon, validate_config
from bot.handlers import register_handlers
from bot.db import database
from aiogram.fsm.storage.memory import MemoryStorage
from bot.utils.spreadsheets import load_volunteer_list, import_stats_from_sheet
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from bot.utils.schedulers import send_weekly_reminder

logger = logging.getLogger(__name__)

async def main():
    configure_logging()
    logger.info('Initializing database...')
    await database.init_db()

    # The database is the configuration's home; the spreadsheet is the fallback
    # for a deployment that has not imported it yet, and the way back if the
    # import turns out wrong. load_from_store() validates before it commits, so
    # a configuration that would not run leaves nothing half-loaded.
    logger.info('Loading lexicon...')
    if not await lexicon.load_from_store():
        logger.info('no configuration in the database, reading the spreadsheet')
        await asyncio.to_thread(Lexicon.load_from_sheet)
    # Fail here, with a specific message, rather than in a handler hours later.
    validate_config()
    logger.info(f'configuration loaded from the {lexicon.source()}')

    logger.info('Synchronizing volunteers...')
    try:
        await database.sync_volunteers(await load_volunteer_list())
    except Exception as e:
        # A Sheets outage must not take the bot offline. load_volunteer_list
        # raises rather than returning an empty roster -- which sync_volunteers
        # would read as "delete every volunteer" -- so carry on with whatever
        # roster the database already holds. With Restart=always, raising here
        # would mean a restart loop every 5 seconds.
        logger.warning(f"Volunteer sync skipped, continuing with the stored roster: {e}")

    logger.info('Importing statistics...')
    await import_stats_from_sheet()

    bot = Bot(token=config.BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    register_handlers(dp)
    scheduler = AsyncIOScheduler(timezone=config.TIMEZONE)
    for level, hour in ((1, 17), (2, 19), (3, 21)):
        scheduler.add_job(
            send_weekly_reminder,
            trigger='cron',
            day_of_week='sun',
            hour=hour,
            minute=0,
            id=f'weekly_reminder_level_{level}',
            name=f'Sunday reminder, level {level}',
            replace_existing=True,
            coalesce=True,
            misfire_grace_time=3600,
            kwargs={'bot': bot, 'level': level},
        )
    scheduler.add_job(
        import_stats_from_sheet,
        trigger='cron',
        hour=0,
        minute=0,
        id='nightly_sheet_import',
        name='Nightly import from the data sheet',
        replace_existing=True,
        coalesce=True,
        misfire_grace_time=3600,
    )
    dp["scheduler"] = scheduler

    scheduler.start()

    # Picks up web UI edits without a restart. Not an APScheduler job: those
    # are cron-shaped, and a 30-second poll with coalescing is simpler as a task.
    config_watcher = asyncio.create_task(lexicon.poll_for_changes())

    logger.info('bot started')
    try:
        await dp.start_polling(bot)
    finally:
        # systemd sends SIGTERM on restart. Close everything so Telegram
        # releases the long poll instead of holding it for several seconds,
        # and so Postgres connections are returned rather than dropped.
        logger.info('shutting down...')
        config_watcher.cancel()
        scheduler.shutdown(wait=False)
        await bot.session.close()
        await database.dispose_db()

if __name__ == "__main__":
    asyncio.run(main())
