import asyncio
from datetime import datetime

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

from bot.config import config
from bot.db import database
from bot.lexicon import fill
from bot.utils.formatters import display_name
from bot.utils.keyboards import get_main_menu_keyboard
from bot.utils.maths import check_week_in_range
from bot.utils.spreadsheets import fetch_users_with_no_stats

# Telegram tolerates roughly 30 messages per second. The previous 0.5 s pause
# after *each* of two sends serialised a 40-volunteer run into ~40 seconds of
# blocked job time.
RATE_LIMIT_DELAY = 0.05


async def resolve_recipients(level: int, user_ids=None) -> list[database.User]:
    """Volunteers who should receive this reminder.

    Skips, with a reason, anyone the sheet names but the database does not
    know, and anyone without a telegram account bound -- both of which used
    to raise inside the send loop (AttributeError on None, or send_message to
    a None chat id).
    """
    if user_ids is not None:
        resolved = []
        for tg_id in user_ids:
            user = await database.get_user_by_tg_id(tg_id)
            if user is None:
                print(f"Reminder skipped: no volunteer bound to tg_id {tg_id}")
            else:
                resolved.append(user)
        return resolved

    current_week = datetime.now().isocalendar()[1]
    names = await fetch_users_with_no_stats(current_week)

    recipients = []
    for name in names:
        user = await database.get_user_by_full_name(name)
        if user is None:
            print(f"Reminder skipped: {name} is in the sheet but not in the database")
        elif user.tg_id is None:
            print(f"Reminder skipped: {name} is not registered")
        else:
            recipients.append(user)
    return recipients


async def send_reminder_to(bot: Bot, user: database.User, level: int) -> None:
    await bot.send_message(
        user.tg_id,
        fill(f"REMINDER{level}", display_name(user.full_name)),
        reply_markup=get_main_menu_keyboard(),
        parse_mode="HTML",
    )


async def send_weekly_reminder(bot: Bot, level: int, user_ids=None):
    if check_week_in_range() is False:
        print("Current week is out of the allowed range. No reminders will be sent.")
        return

    try:
        recipients = await resolve_recipients(level, user_ids)
    except Exception as e:
        # fetch_users_with_no_stats raises on a Sheets failure now; tell the
        # admin rather than letting the job die silently in the scheduler log.
        print(f"Reminder level {level} aborted: {e}")
        await report_to_admin(bot, level, [], [f"could not determine recipients: {e}"])
        return

    reminded: list[str] = []
    failed: list[str] = []

    for user in recipients:
        try:
            await send_reminder_to(bot, user, level)
            reminded.append(user.full_name)
        except TelegramForbiddenError:
            failed.append(f"{user.full_name}: blocked the bot")
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            try:
                # Retry the reminder itself. This used to send the literal
                # string "..." instead, so the volunteer never got the message.
                await send_reminder_to(bot, user, level)
                reminded.append(user.full_name)
            except Exception as retry_error:
                failed.append(f"{user.full_name}: {retry_error}")
        except Exception as e:
            failed.append(f"{user.full_name}: {e}")

        await asyncio.sleep(RATE_LIMIT_DELAY)

    await report_to_admin(bot, level, reminded, failed)


def format_admin_summary(level: int, reminded: list[str], failed: list[str]) -> str:
    """One summary per run.

    The admin previously received a separate direct message for every single
    volunteer reminded -- three times over, once per escalation level.
    """
    lines = [f"Reminder level {level}: {len(reminded)} sent, {len(failed)} failed"]
    if reminded:
        lines.append("Sent: " + ", ".join(reminded))
    if failed:
        lines.append("Failed:")
        lines.extend(f"  {entry}" for entry in failed)
    return "\n".join(lines)


async def report_to_admin(bot: Bot, level: int, reminded: list[str], failed: list[str]) -> None:
    if not config.ADMIN_IDS:
        return
    try:
        await bot.send_message(
            config.ADMIN_IDS[0], format_admin_summary(level, reminded, failed)
        )
    except Exception as e:
        print(f"Failed to send the reminder summary to the admin: {e}")
