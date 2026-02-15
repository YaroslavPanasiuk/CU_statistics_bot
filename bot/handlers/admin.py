# handlers/admin.py
from aiogram import Router, types
from bot.filters.is_admin import IsAdmin
from bot.utils.schedulers import send_weekly_reminder
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram.filters import Command, CommandObject
from bot.lexicon import select_random_line
from bot.db import database
from bot.utils.keyboards import get_main_menu_keyboard

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
        next_run = job.next_run_time.strftime("%Y-%m-%d %H:%M:%S") if job.next_run_time else "Paused"
        response += (
            f"🆔 <b>ID:</b> <code>{job.id}</code>\n"
            f"📝 <b>Name:</b> {job.name}\n"
            f"🕒 <b>Next Run:</b> {next_run}\n"
            f"-------------------\n"
        )

    await message.answer(response, parse_mode="HTML")


@router.message(Command("remind_volunteers"), IsAdmin())
async def cmd_list_jobs(message: types.Message, command: CommandObject):
    if not command.args:
        await message.answer("Please provide reminder level. Usage: /remind_volunteers [level]")
        return

    if command.args.isdigit():
        level = int(command.args)
        await send_weekly_reminder(message.bot, level)
    else:
        await message.answer("Invalid level format. Please use a number.")


@router.message(Command("remind_admin"), IsAdmin())
async def cmd_list_jobs(message: types.Message, command: CommandObject):
    if not command.args:
        await message.answer("Please provide reminder level. Usage: /remind_admin [level]")
        return

    if command.args.isdigit():
        level = int(command.args)
        user_ids = [message.from_user.id]
        await send_weekly_reminder(message.bot, level, user_ids)
    else:
        await message.answer("Invalid level format. Please use a number.")
