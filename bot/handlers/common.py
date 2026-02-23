from aiogram import Router, types
from aiogram.filters import CommandStart, Command, or_f
from aiogram.fsm.context import FSMContext
from bot.db import database
from aiogram.fsm.state import StatesGroup, State
from bot.lexicon import select_random_line, LexiconFilter
from datetime import datetime, timedelta
from bot.utils.maths import questions_in_week
from bot.utils.formatters import week_num_to_dates, random_bible_verse
from bot.utils.keyboards import get_weeks_keyboard, get_unregistered_keyboard, WeekCallback, get_main_menu_keyboard
from bot.utils.spreadsheets import export_stats_to_sheet
from bot.filters.is_registered import IsNotRegistered
from aiogram import F
from bot.config import Config

class Registration(StatesGroup):
    waiting_for_name = State()

class StatisticsCollection(StatesGroup):
    waiting_for_week = State()
    waiting_for_answer = State()

register_router = Router()
router = Router()
register_router.message.filter(IsNotRegistered())

@router.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    if await database.is_user_registered(message.from_user.id):
        user = await database.get_user_by_tg_id(message.from_user.id)
        await message.answer(f"Привіт, {user.full_name.split()[1]}!", reply_markup=get_main_menu_keyboard())
        return
    unregistered = await database.get_unregistered_users()
    
    if not unregistered:
        await message.answer("Всі волонтери вже зареєстровані.")
        return

    await message.answer(select_random_line('SELECT_YOUR_NAME'), reply_markup=get_unregistered_keyboard(unregistered))
    await message.answer("Меню:", reply_markup=get_main_menu_keyboard())



@register_router.message()
async def not_registered(message: types.Message):
    await message.answer(select_random_line('NOT_REGISTERED'))

@router.callback_query(F.data.startswith("reg_"))
async def complete_registration(callback: types.CallbackQuery):
    user_db_id = int(callback.data.split("_")[1])
    tg_id = callback.from_user.id
    
    success = await database.register_user(user_db_id, tg_id)
    user = await database.get_user_by_tg_id(tg_id)
    
    if success:
        await callback.message.edit_text(select_random_line('REGISTRATION_COMPLETE').format(user.full_name.split()[1]))
        await callback.message.edit_reply_markup(types.ReplyKeyboardRemove)
    else:
        await callback.answer("Error: Name already taken or not found.", show_alert=True)


async def initiate_stats_questions(message: types.Message, state: FSMContext, week_num: int):
    await state.update_data(selected_week=week_num)

    await message.answer(select_random_line('SELECT_WEEK'))
    await message.answer(
        f"{select_random_line("QUESTION_1")}",
        reply_markup=types.ReplyKeyboardRemove()
    )
    await state.set_state(StatisticsCollection.waiting_for_answer)

@router.message(or_f(Command("fill_stats"),LexiconFilter("FILL_STATISTICS")))
async def cmd_fill_stats(message: types.Message, state: FSMContext):
    current_week = (datetime.now() - timedelta(days=Config.LAG_TRESHOLD_DAYS)).isocalendar()[1]
    await initiate_stats_questions(message, state, current_week)

@router.message(or_f(Command("fill_old_stats"),LexiconFilter("SELECT_PREVIOUS_WEEK")))
async def start_old_stats(message: types.Message, state: FSMContext):
    kb = await get_weeks_keyboard(message.from_user.id)
    await state.set_state(StatisticsCollection.waiting_for_week)

@router.callback_query(StatisticsCollection.waiting_for_week, WeekCallback.filter())
async def process_week_callback(
    callback: types.CallbackQuery, 
    callback_data: WeekCallback, 
    state: FSMContext
):
    week_num = callback_data.number
    
    await callback.message.edit_text(f"{select_random_line('SELECTED_WEEK')} {week_num} ({week_num_to_dates(week_num)})")
    
    await initiate_stats_questions(callback.message, state, week_num)
    
    await callback.answer()


@router.message(StatisticsCollection.waiting_for_answer)
async def process_any_answer(message: types.Message, state: FSMContext):
    data = await state.get_data()
    current_index = get_next_question_index(data)
    
    await state.update_data({f"answer_{current_index}": message.text})
    await ask_next_question_or_finish(message, state)


async def finalize_and_save_stats(message: types.Message, state: FSMContext):
    user_data = await state.get_data()
    
    week_num = user_data.get("selected_week")
    if not week_num:
        await message.answer("Error: Week data missing. Please restart.")
        await state.clear()
        return
    stats_dict = {k: v for k, v in user_data.items() if k != "selected_week"}
    
    await database.save_user_stats(
        tg_id=message.from_user.id,
        week=week_num,
        data_dict=stats_dict
    )
    
    await message.answer(select_random_line('STATISTICS_GATHERED'), reply_markup=get_main_menu_keyboard())
    await state.clear()
    await export_stats_to_sheet(message.from_user.id, week_num)


def get_next_question_index(user_data: dict) -> int:
    answers = [k for k in user_data.keys() if k.startswith("answer_")]
    return len(answers) + 1

async def ask_next_question_or_finish(message: types.Message, state: FSMContext):
    data = await state.get_data()
    week = data.get('selected_week')
    next_index = get_next_question_index(data)
    
    if next_index > questions_in_week(week):
        await finalize_and_save_stats(message, state)
    else:
        question_key = f"QUESTION_{next_index}"
        text = select_random_line(question_key)
        
        await message.answer(text)
        await state.set_state(StatisticsCollection.waiting_for_answer)


@router.message(~F.text.startswith("/"))
async def not_registered(message: types.Message):
    await message.answer(random_bible_verse())