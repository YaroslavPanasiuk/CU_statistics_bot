# handlers/admin.py
from aiogram import Router, types
from bot.filters.is_admin import IsAdmin
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram.filters import Command

router = Router()
router.message.filter(IsAdmin())


@router.message(Command("list_jobs"), IsAdmin())
async def cmd_list_jobs(message: types.Message, scheduler: AsyncIOScheduler):
    jobs = scheduler.get_jobs()
    
    if not jobs:
        await message.answer("No active scheduled jobs.")
        return

    response = "<b>Active Scheduler Jobs:</b>\n\n"
    for job in jobs:
        # job.next_run_time tells us when it triggers next
        next_run = job.next_run_time.strftime("%Y-%m-%d %H:%M:%S") if job.next_run_time else "Paused"
        response += (
            f"🆔 <b>ID:</b> <code>{job.id}</code>\n"
            f"📝 <b>Name:</b> {job.name}\n"
            f"🕒 <b>Next Run:</b> {next_run}\n"
            f"-------------------\n"
        )

    await message.answer(response, parse_mode="HTML")