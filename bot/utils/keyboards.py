from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from datetime import datetime, timedelta
from bot.lexicon import Lexicon, select_random_line
from bot.utils.formatters import week_num_to_dates
from aiogram.filters.callback_data import CallbackData
from aiogram.types import ReplyKeyboardMarkup
from bot.db import database

def get_main_menu_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.button(text=select_random_line('FILL_STATISTICS'))
    builder.button(text=select_random_line('SELECT_PREVIOUS_WEEK'))
    
    builder.adjust(1)
    
    return builder.as_markup(
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Select an action..."
    )

class WeekCallback(CallbackData, prefix="week"):
    number: int

async def get_weeks_keyboard(tg_id):
    builder = InlineKeyboardBuilder()
    current_week = datetime.now().isocalendar()[1]
    start_week = datetime.strptime(Lexicon.START_DATE, "%d.%m.%Y").isocalendar()[1]
    for i in range(start_week, current_week+1):
        stats = await database.get_user_statistics(tg_id, i)
        stats_str = "не заповнено"
        if len(stats) > 0:
            stats_str = ", ".join(list(stats[0][1].values()))
        builder.button(
            text=f"Тиждень {i} ({week_num_to_dates(i)}) - {stats_str}",
            callback_data=WeekCallback(number=i).pack()
        )
    
    builder.adjust(1)
    return builder.as_markup(resize_keyboard=True, one_time_keyboard=True)

def get_unregistered_keyboard(users):    
    builder = InlineKeyboardBuilder()
    for user in users:
        builder.button(text=user.full_name, callback_data=f"reg_{user.id}")
    
    builder.adjust(2)
    return builder.as_markup()