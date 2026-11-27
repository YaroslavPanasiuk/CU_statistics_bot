import logging
from aiogram import Router, types
from aiogram.filters import CommandStart, Command, or_f
from aiogram.fsm.context import FSMContext
from bot.db import database
from aiogram.fsm.state import StatesGroup, State
from bot.lexicon import select_random_line, fill, LexiconFilter
from datetime import datetime, timedelta
from bot.utils.maths import questions_in_week
from bot.utils.formatters import week_num_to_dates, random_bible_verse, format_all_stats_for_user, display_name
from bot.utils.keyboards import get_weeks_keyboard, WeekCallback, get_main_menu_keyboard
from bot.utils.spreadsheets import export_stats_to_sheet
from bot.filters.is_registered import IsNotRegistered, IsRegistered
from bot.filters.bot_available import BotAvailable
from aiogram import F
from bot.lexicon import Lexicon
from bot.config import config

logger = logging.getLogger(__name__)

class Registration(StatesGroup):
    waiting_for_name = State()

class StatisticsCollection(StatesGroup):
    waiting_for_week = State()
    waiting_for_answer = State()

registered_router = Router()
registered_router.message.filter(IsRegistered())

# Unregistered users only: this router ends in a "you are not registered"
# catch-all, which previously answered registered users too.
router = Router()
router.message.filter(IsNotRegistered())

# Included last, after admin.router, so an unknown-command reply can never
# shadow an admin command (admins are registered volunteers as well).
fallback_router = Router()
fallback_router.message.filter(IsRegistered())

unavailable_router = Router()
unavailable_router.message.filter(~BotAvailable())



def normalise_name(name: str) -> str:
    return " ".join(name.split()).casefold()


async def find_unregistered_volunteer(typed: str):
    """The one unregistered volunteer matching this name, or None.

    Returns None when nothing matches *and* when several things do, so the
    caller always answers the same way and the roster cannot be probed.
    """
    candidates = await database.get_unregistered_users()
    wanted = normalise_name(typed)

    matches = [u for u in candidates if normalise_name(u.full_name) == wanted]
    if len(matches) == 1:
        return matches[0]

    # The sheet stores "Surname Given"; people often type it the other way
    # round. Compare the tokens without regard to order.
    wanted_tokens = sorted(wanted.split())
    if wanted_tokens:
        matches = [
            u for u in candidates
            if sorted(normalise_name(u.full_name).split()) == wanted_tokens
        ]
        if len(matches) == 1:
            return matches[0]

    return None


@router.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    # This used to reply with an inline keyboard listing every unregistered
    # volunteer by full name, to anyone who found the bot, and accept any
    # reg_<id> callback -- so a stranger could read the roster and then claim
    # someone's identity. The user names themselves instead.
    await message.answer(
        select_random_line('SELECT_YOUR_NAME'),
        reply_markup=types.ReplyKeyboardRemove(),
    )
    await state.set_state(Registration.waiting_for_name)


@router.message(Registration.waiting_for_name, F.text)
async def process_registration_name(message: types.Message, state: FSMContext):
    volunteer = await find_unregistered_volunteer(message.text)

    # TODO(phase-7): move these strings into the config sheet.
    if volunteer is None or not await database.register_user(volunteer.id, message.from_user.id):
        # Deliberately identical for "no such volunteer", "already registered"
        # and "ambiguous": a specific answer would let anyone test the roster
        # one name at a time.
        await message.answer("Не вдалося тебе знайти. Перевір написання і спробуй ще раз.")
        return

    await state.clear()
    await message.answer(
        fill('REGISTRATION_COMPLETE', display_name(volunteer.full_name)),
        reply_markup=get_main_menu_keyboard(),
    )


@registered_router.message(CommandStart())
async def cmd_start_registered(message: types.Message, state: FSMContext, volunteer: database.User):
    await message.answer(
        f"Привіт, {display_name(volunteer.full_name)}!",
        reply_markup=get_main_menu_keyboard(),
    )


async def initiate_stats_questions(
    message: types.Message, state: FSMContext, week_num: int, announce: bool = True
):
    """Start the questionnaire for a week.

    Refuses a week that asks nothing. It used to send QUESTION_1 regardless,
    so an out-of-range week collected one answer and saved it -- which is how
    the stray week-33 row got into production.
    """
    if questions_in_week(week_num) < 1:
        await message.answer(select_random_line('BOT_UNAVAILABLE'))
        await state.clear()
        return

    await state.update_data(selected_week=week_num)

    if announce:
        await message.answer(
            f"{select_random_line('SELECTED_WEEK')} {week_num} ({week_num_to_dates(week_num)})"
        )
    await message.answer(
        select_random_line("QUESTION_1"),
        reply_markup=types.ReplyKeyboardRemove(),
    )
    await state.set_state(StatisticsCollection.waiting_for_answer)

@registered_router.message(or_f(Command("fill_stats"),LexiconFilter("FILL_STATISTICS")))
async def cmd_fill_stats(message: types.Message, state: FSMContext):
    current_week = (datetime.now() - timedelta(days=config.LAG_THRESHOLD_DAYS)).isocalendar()[1]
    await initiate_stats_questions(message, state, current_week)

@registered_router.message(or_f(Command("fill_old_stats"),LexiconFilter("SELECT_PREVIOUS_WEEK")))
async def start_old_stats(message: types.Message, state: FSMContext, volunteer: database.User):
    kb = await get_weeks_keyboard(volunteer.id)
    await message.answer(select_random_line('SELECT_WEEK'), reply_markup=kb)
    await state.set_state(StatisticsCollection.waiting_for_week)

@registered_router.callback_query(StatisticsCollection.waiting_for_week, WeekCallback.filter())
async def process_week_callback(
    callback: types.CallbackQuery,
    callback_data: WeekCallback,
    state: FSMContext
):
    week_num = callback_data.number

    # The edit replaces the week-picker with the choice, so initiate must not
    # announce it a second time.
    await callback.message.edit_text(
        f"{select_random_line('SELECTED_WEEK')} {week_num} ({week_num_to_dates(week_num)})"
    )
    await initiate_stats_questions(callback.message, state, week_num, announce=False)

    await callback.answer()


@registered_router.message(Command("cancel"))
async def cmd_cancel(message: types.Message, state: FSMContext):
    # TODO(phase-7): move these strings into the config sheet with the other
    # hardcoded Ukrainian text.
    if await state.get_state() is None:
        await message.answer("Немає чого скасовувати.", reply_markup=get_main_menu_keyboard())
        return
    await state.clear()
    await message.answer("Скасовано.", reply_markup=get_main_menu_keyboard())


@registered_router.message(Command("unregister_me"))
async def cmd_unregister_me(message: types.Message, state: FSMContext, volunteer: database.User):
    await database.unregister_user(volunteer.tg_id)
    await state.clear()
    # TODO(phase-7): move into the config sheet.
    await message.answer(
        "Реєстрацію скасовано. Надішли /start, щоб зареєструватися знову.",
        reply_markup=types.ReplyKeyboardRemove(),
    )


@registered_router.message(or_f(Command("get_my_stats"),LexiconFilter("GET_MY_STATS")))
async def get_my_stats(message: types.Message, volunteer: database.User):
    user_stats = await database.get_all_statistics_for_user(
        volunteer.id,
        datetime.strptime(Lexicon.START_DATE, "%d.%m.%Y").isocalendar()[1],
        datetime.strptime(Lexicon.END_DATE, "%d.%m.%Y").isocalendar()[1],
    )

    if not user_stats:
        await message.answer(select_random_line('NO_STATS_AVAILABLE'))
        return

    await message.answer(format_all_stats_for_user(user_stats), parse_mode="HTML")


@registered_router.message(StatisticsCollection.waiting_for_answer, F.text & ~F.text.startswith("/"))
async def process_any_answer(message: types.Message, state: FSMContext, volunteer: database.User):
    answer = parse_answer(message.text)
    if answer is None:
        # Stay on the same question. Unvalidated text used to be stored as-is
        # and silently counted as 0 when the totals were rendered.
        # TODO(phase-7): move this string into the config sheet.
        await message.answer("Введи, будь ласка, число — наприклад 0 або 3.")
        return

    data = await state.get_data()
    current_index = get_next_question_index(data)
    await state.update_data({f"answer_{current_index}": answer})
    await ask_next_question_or_finish(message, state, volunteer)


async def finalize_and_save_stats(message: types.Message, state: FSMContext, volunteer: database.User):
    user_data = await state.get_data()

    week_num = user_data.get("selected_week")
    if not week_num:
        await message.answer("Error: Week data missing. Please restart.")
        await state.clear()
        return
    stats_dict = {k: v for k, v in user_data.items() if k != "selected_week"}

    logger.info(f"User {volunteer.full_name} has filled stats for week {week_num}: {stats_dict.values()}")

    await database.save_user_stats(
        volunteer_id=volunteer.id,
        week=week_num,
        data_dict=stats_dict
    )

    await message.answer(select_random_line('STATISTICS_GATHERED'), reply_markup=get_main_menu_keyboard())
    await state.clear()
    await export_stats_to_sheet(volunteer, week_num)


def parse_answer(text: str | None) -> str | None:
    """Normalise an answer, or None if it is not a count.

    Answers are summed as integers when statistics are rendered, so anything
    else was silently counted as 0 with no indication to the volunteer.
    Returns a string because that is what the sheet and the JSONB column hold;
    normalising through int() also turns "007" into "7".
    """
    if text is None:
        return None
    cleaned = text.strip()
    if not cleaned or len(cleaned) > config.MAX_ANSWER_LENGTH:
        return None
    # isdigit() alone accepts superscripts and other unicode digit forms.
    if not (cleaned.isascii() and cleaned.isdigit()):
        return None
    return str(int(cleaned))


def get_next_question_index(user_data: dict) -> int:
    answers = [k for k in user_data.keys() if k.startswith("answer_")]
    return len(answers) + 1

async def ask_next_question_or_finish(message: types.Message, state: FSMContext, volunteer: database.User):
    data = await state.get_data()
    week = data.get('selected_week')
    next_index = get_next_question_index(data)

    if next_index > questions_in_week(week):
        await finalize_and_save_stats(message, state, volunteer)
    else:
        question_key = f"QUESTION_{next_index}"
        text = select_random_line(question_key)

        await message.answer(text)
        await state.set_state(StatisticsCollection.waiting_for_answer)

@registered_router.message(~F.text.startswith("/"))
async def default_handler(message: types.Message):
    await message.answer(random_bible_verse())

@unavailable_router.message()
async def bot_unavailable(message: types.Message):
    now = datetime.now()
    if now < datetime.strptime(Lexicon.START_DATE, "%d.%m.%Y"):
        await message.answer(select_random_line('SEMESTER_NOT_STARTED'))
    elif now > datetime.strptime(Lexicon.END_DATE, "%d.%m.%Y"):
        await message.answer(select_random_line('SEMESTER_OVER'))
    else:
        await message.answer(select_random_line('BOT_UNAVAILABLE'))

@router.message()
async def not_registered(message: types.Message):
    await message.answer(select_random_line('NOT_REGISTERED'))


@fallback_router.message(F.text.startswith("/"))
async def unknown_command(message: types.Message):
    # TODO(phase-7): move this string into the config sheet with the other
    # hardcoded Ukrainian text.
    await message.answer("Невідома команда.", reply_markup=get_main_menu_keyboard())
