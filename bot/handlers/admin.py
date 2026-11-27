# handlers/admin.py
import asyncio
from aiogram import Bot, Router, types
from bot.filters.is_admin import IsAdmin
from bot.utils.schedulers import RATE_LIMIT_DELAY, send_weekly_reminder
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram.exceptions import TelegramRetryAfter
from aiogram.filters import Command, CommandObject
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from bot.db import database
from bot import lexicon
from bot.lexicon import Lexicon, validate_config
from bot.utils.spreadsheets import import_stats_from_sheet, reset_sheet_cache

router = Router()
router.message.filter(IsAdmin())
# The broadcast confirmation is a callback, which the message filter above
# does not cover.
router.callback_query.filter(IsAdmin())


class Broadcast(StatesGroup):
    waiting_for_confirmation = State()


class BroadcastCallback(CallbackData, prefix="bcast"):
    send: bool


def reminder_level_or_error(raw: str | None, usage: str) -> tuple[int | None, str | None]:
    """Parse and bounds-check a reminder level.

    An out-of-range level used to raise inside the per-user send loop, so the
    admin got no feedback at all while every recipient silently failed.
    """
    if not raw:
        return None, f"Please provide a reminder level. Usage: {usage}"
    raw = raw.strip()
    if not (raw.isascii() and raw.isdigit()):
        return None, "Invalid level format. Please use a number."
    level = int(raw)
    if getattr(Lexicon, f"REMINDER{level}", None) in (None, ""):
        return None, f"The config sheet has no REMINDER{level} key."
    return level, None


@router.message(Command("list_jobs"))
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


@router.message(Command("remind_volunteers"))
async def cmd_remind_volunteers(message: types.Message, command: CommandObject):
    level, error = reminder_level_or_error(command.args, "/remind_volunteers [level]")
    if error:
        await message.answer(error)
        return
    await send_weekly_reminder(message.bot, level)


@router.message(Command("remind_admin"))
async def cmd_remind_admin(message: types.Message, command: CommandObject):
    level, error = reminder_level_or_error(command.args, "/remind_admin [level]")
    if error:
        await message.answer(error)
        return
    await send_weekly_reminder(message.bot, level, [message.from_user.id])


@router.message(Command("broadcast"))
async def cmd_broadcast(message: types.Message, command: CommandObject, state: FSMContext):
    if not command.args:
        return await message.answer("Usage: /broadcast [your message]")

    recipients = await database.get_all_registered_ids()
    if not recipients:
        return await message.answer("No registered users to broadcast to.")

    await state.update_data(broadcast_text=command.args)
    await state.set_state(Broadcast.waiting_for_confirmation)

    builder = InlineKeyboardBuilder()
    builder.button(text=f"Send to {len(recipients)}", callback_data=BroadcastCallback(send=True))
    builder.button(text="Cancel", callback_data=BroadcastCallback(send=False))
    await message.answer(
        f"Broadcast to {len(recipients)} users?\n\n{command.args}",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(Broadcast.waiting_for_confirmation, BroadcastCallback.filter())
async def confirm_broadcast(
    callback: types.CallbackQuery,
    callback_data: BroadcastCallback,
    state: FSMContext,
    bot: Bot,
):
    text = (await state.get_data()).get("broadcast_text")
    await state.clear()
    await callback.answer()

    if not callback_data.send or not text:
        await callback.message.edit_text("Broadcast cancelled.")
        return

    recipients = await database.get_all_registered_ids()
    await callback.message.edit_text(f"Broadcasting to {len(recipients)} users...")

    sent, failed = 0, []
    for tg_id in recipients:
        try:
            await bot.send_message(tg_id, text)
            sent += 1
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            try:
                await bot.send_message(tg_id, text)
                sent += 1
            except Exception as retry_error:
                failed.append(f"{tg_id}: {retry_error}")
        except Exception as e:
            failed.append(f"{tg_id}: {e}")
        await asyncio.sleep(RATE_LIMIT_DELAY)

    summary = f"Broadcast complete: {sent} sent, {len(failed)} failed."
    if failed:
        summary += "\n" + "\n".join(failed[:20])
    await callback.message.answer(summary)


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
    tg_id_str = tg_id_str.strip()
    if not (tg_id_str.isascii() and tg_id_str.isdigit()):
        await message.answer(f"{tg_id_str!r} is not a numeric telegram id.")
        return
    tg_id = int(tg_id_str)
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

@router.message(Command("reload_config"))
async def cmd_reload_config(message: types.Message, command: CommandObject):
    """Reload now instead of waiting for the poll.

    Reads whichever source is in charge, so this keeps working either side of
    the cutover. A reload that fails leaves the running configuration alone.
    """
    try:
        if not await lexicon.load_from_store():
            await asyncio.to_thread(Lexicon.load_from_sheet)
            validate_config()
        # WORKSHEET_NAME may now point at a different tab.
        reset_sheet_cache()
        await message.answer(f"Successfully reloaded config from the {lexicon.source()}")
    except Exception as e:
        await message.answer(f"Failed to reload config: {e}")

@router.message(Command("reload_data"))
async def cmd_reload_data(message: types.Message, command: CommandObject):
    try:
        await import_stats_from_sheet()
        await message.answer("Successfully reloaded data from sheet")
    except Exception as e:
        await message.answer(f"Failed to reload data from sheet: {e}")
