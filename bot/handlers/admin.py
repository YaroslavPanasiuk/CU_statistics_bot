# handlers/admin.py
import asyncio
from aiogram import Bot, Router, types
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


@router.message(Command("broadcast"), IsAdmin())
async def cmd_broadcast(message: types.Message, command: CommandObject, bot: Bot):
    if not command.args:
        return await message.answer("Usage: /broadcast [your message]")

    user_ids = await database.get_all_registered_ids()
    count = 0
    
    await message.answer(f"🚀 Starting broadcast to {len(user_ids)} users...")

    for tg_id in user_ids:
        try:
            await bot.send_message(tg_id, command.args)
            count += 1
            await asyncio.sleep(0.05) 
        except Exception as e:
            print(f"Failed to send to {tg_id}: {e}")

    await message.answer(f"✅ Broadcast complete. Sent to {count} users.")


@router.message(Command("unregister_user"))
async def cmd_unregister_user(message: types.Message, command: CommandObject):
    if not command.args:
        await message.answer("Usage: /unregister_user [tg_id] or /unregister_user [name]")
        return
    arg = command.args.strip()

    if arg.isdigit():
        tg_id = int(arg)
        success = await database.unregister_user(tg_id)
        if success:
            await message.answer(f"User with tg_id {tg_id} has been unregistered.")
            return
        await message.answer(f"No user found with tg_id {tg_id}.")
        return
    
    user = await database.get_user_by_full_name(arg)
    if user and user.tg_id:
        success = await database.unregister_user(user.tg_id)
        if success:
            await message.answer(f"User {arg} has been unregistered.")
            return
        await message.answer(f"Failed to unregister user {arg}.")
        return
    await message.answer(f"No registered user found with name {arg}.")


@router.message(Command("register_user"))
async def cmd_register_user(message: types.Message, command: CommandObject):
    if not command.args or "," not in command.args or len(command.args.split(",")) != 2:
        await message.answer("Usage: /register_user [full_name],[tg_id]")
        return
    full_name, tg_id_str = command.args.split(",", 1)
    full_name = full_name.strip()
    tg_id = int(tg_id_str.strip())
    user = await database.get_user_by_full_name(full_name)
    if not user:
        await message.answer(f"No user found with name {full_name}.")
        return
    if user.tg_id:
        await message.answer(f"User {full_name} is already registered with tg_id {user.tg_id}.")
        return
    success = await database.register_user(user.id, tg_id)
    if success:
        await message.answer(f"User {full_name} has been registered with tg_id {tg_id}.")
    else:        
        await message.answer(f"Failed to register user {full_name} with tg_id {tg_id}.")
