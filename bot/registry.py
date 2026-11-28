"""What every configuration key means, and how it may be edited.

The spreadsheet was hard to understand not because it was a spreadsheet, but
because it was a flat list of 44 names. Nothing told a reader that
``QUESTION_PATTERN`` is a cycling digit string that also decides which
spreadsheet columns a week's answers live in, that ``REMINDER2`` is the second
of three Sunday escalations, or that ``GREETING`` is the bot's introduction and
not a per-person hello.

That ambiguity has caused real outages and real wrong messages:

* ``NO_STATS_AVAILABLE`` was required by startup validation but absent from the
  sheet, so the bot exited on boot and systemd relaunched it every five seconds
  for twelve hours.
* A fallback named ``GREETING`` silently picked up the sheet's existing
  ``GREETING``, so a registered volunteer's /start replied with the whole
  264-character introduction instead of their name.
* ``SEMESTER_NOT_STARTED`` holds a ``{0}`` but was sent without substitution,
  so volunteers saw a literal "{0}".
* ``SELECT_YOUR_NAME`` still said "choose yourself from the list" months after
  the list was removed.

This registry is the answer. It is the single description of the configuration
surface: the config UI renders its forms and validation from here, the importer
uses it to decide which keys hold variant lists, and the test suite fails if
code references a key this registry does not describe, or describes a key no
code reads. Values live in the database; meaning lives here, next to the
handlers that use it.
"""

from dataclasses import dataclass, field
from enum import StrEnum

#: Telegram rejects messages longer than this.
TELEGRAM_MESSAGE_LIMIT = 4096
#: Reply-keyboard labels have to fit on a phone screen.
BUTTON_LABEL_LIMIT = 64
#: Short labels shown in the statistics summary, padded to a fixed width.
STAT_LABEL_LIMIT = 40
#: The highest QUESTION_N the pattern may refer to.
MAX_QUESTIONS = 8


class Kind(StrEnum):
    """How a value is edited and validated."""

    TEXT = "text"            # plain message text
    TEMPLATE = "template"    # str.format() with a positional placeholder
    BUTTON = "button"        # reply-keyboard label, matched against incoming text
    STAT_LABEL = "stat_label"  # short name shown in the totals table
    DATE = "date"            # dd.mm.yyyy
    PATTERN = "pattern"      # QUESTION_PATTERN: one digit per week, cycling
    WORKSHEET = "worksheet"  # a tab name in the data spreadsheet


class Group(StrEnum):
    """Sections of the config UI, in the order they appear."""

    SCHEDULE = "schedule"
    QUESTIONS = "questions"
    MENU = "menu"
    REGISTRATION = "registration"
    REMINDERS = "reminders"
    SERVICE = "service"
    UNUSED = "unused"


GROUP_LABELS_UK: dict[Group, str] = {
    Group.SCHEDULE: "Семестр і розклад питань",
    Group.QUESTIONS: "Питання анкети",
    Group.MENU: "Кнопки меню",
    Group.REGISTRATION: "Реєстрація",
    Group.REMINDERS: "Нагадування",
    Group.SERVICE: "Службові повідомлення",
    Group.UNUSED: "Не використовується",
}

GROUP_HELP_UK: dict[Group, str] = {
    Group.SCHEDULE: (
        "Дати семестру та скільки питань бот задає щотижня. Ці значення також "
        "визначають, у які стовпці таблиці потрапляють відповіді, тому змінюйте "
        "їх обережно."
    ),
    Group.QUESTIONS: (
        "Самі питання та їхні короткі назви для підсумкової таблиці. Питань "
        "потрібно стільки, скільки вимагає найбільша цифра в розкладі."
    ),
    Group.MENU: "Підписи кнопок. Бот порівнює надіслане повідомлення саме з цим текстом.",
    Group.REGISTRATION: "Те, що бачить волонтер, коли вперше пише боту.",
    Group.REMINDERS: "Три нагадування щонеділі: 17:00, 19:00 і 21:00.",
    Group.SERVICE: "Підтвердження, помилки та інші короткі відповіді.",
    Group.UNUSED: (
        "Залишилися в таблиці, але жодна частина бота їх не читає. Можна "
        "видалити — або використати, якщо хочете повернути ці екрани."
    ),
}


@dataclass(frozen=True)
class Field:
    """One configuration key."""

    key: str
    kind: Kind
    group: Group
    label_uk: str
    help_uk: str
    #: Placeholders the value must contain, e.g. ("{0}",) for a name.
    placeholders: tuple[str, ...] = ()
    #: True when several ';;' variants are meaningful and one is picked at random.
    variants: bool = False
    #: False when the bot has a built-in fallback and can start without this key.
    required: bool = True
    #: Longest permitted value; defaults to Telegram's message limit.
    limit: int = TELEGRAM_MESSAGE_LIMIT
    #: Only required when the question schedule actually asks for it.
    conditional: bool = False
    #: Reached by a computed name (f"REMINDER{level}"), so the key never
    #: appears literally in the source and a scan cannot find it.
    dynamic: bool = False
    #: Sent with parse_mode="HTML", so Telegram parses the markup and rejects
    #: the whole message if it does not parse. Almost nothing is: the
    #: volunteer-facing handlers call message.answer() with no parse_mode, so
    #: their text is plain. Reminders are the exception -- REMINDER3 carries an
    #: <a href> to the spreadsheet -- and a stray '<' there means every
    #: volunteer's reminder fails to send.
    html: bool = False
    aliases: tuple[str, ...] = field(default=(), repr=False)


def _question_fields() -> tuple[Field, ...]:
    """QUESTION_1..N and their short names.

    Generated rather than written out: how many are needed depends on the
    largest digit in QUESTION_PATTERN, so the registry describes all of them
    and validation decides which are mandatory.
    """
    fields: list[Field] = []
    for n in range(1, MAX_QUESTIONS + 1):
        fields.append(Field(
            f"QUESTION_{n}", Kind.TEXT, Group.QUESTIONS,
            f"Питання {n}",
            f"{n}-е питання, яке бот задає під час заповнення статистики. "
            "Відповідь має бути числом.",
            variants=True, conditional=True,
        ))
        fields.append(Field(
            f"QUESTION_{n}_NAME", Kind.STAT_LABEL, Group.QUESTIONS,
            f"Коротка назва питання {n}",
            "Підпис у таблиці «моя статистика». Довгі назви обрізаються, "
            f"тому тримайтеся приблизно {STAT_LABEL_LIMIT} символів.",
            limit=STAT_LABEL_LIMIT, conditional=True,
        ))
    return tuple(fields)


FIELDS: tuple[Field, ...] = (
    # ------------------------------------------------------------- schedule --
    Field(
        "START_DATE", Kind.DATE, Group.SCHEDULE,
        "Початок семестру",
        "Перший день семестру, у форматі дд.мм.рррр. До цієї дати бот не "
        "збирає статистику. Мусить бути того ж календарного року, що й кінець.",
        limit=10,
    ),
    Field(
        "END_DATE", Kind.DATE, Group.SCHEDULE,
        "Кінець семестру",
        "Останній день семестру, у форматі дд.мм.рррр. Має бути того ж "
        "календарного року, що й початок: уся арифметика тижнів працює з "
        "номерами ISO-тижнів, тому семестр через Новий рік неможливий.",
        limit=10,
    ),
    Field(
        "QUESTION_PATTERN", Kind.PATTERN, Group.SCHEDULE,
        "Розклад питань",
        "Рядок цифр: одна цифра — один тиждень, скільком питанням він "
        "відповідає. Після останньої цифри розклад починається спочатку. "
        "Наприклад «4» означає по 4 питання щотижня, «323» — 3, 2, 3, далі "
        "знову 3. Ці цифри також визначають, у які стовпці таблиці пишуться "
        "відповіді, тому зміна зсуває всю розмітку.",
        limit=60,
    ),
    Field(
        "WORKSHEET_NAME", Kind.WORKSHEET, Group.SCHEDULE,
        "Аркуш із даними",
        "Назва вкладки в таблиці даних, де лежить список волонтерів і "
        "відповіді. Інші вкладки бот не читає й не змінює.",
        limit=100,
    ),

    # ------------------------------------------------------------ questions --
    *_question_fields(),

    # ----------------------------------------------------------------- menu --
    Field(
        "FILL_STATISTICS", Kind.BUTTON, Group.MENU,
        "Кнопка «Заповнити статистику»",
        "Починає заповнення за поточний тиждень.",
        limit=BUTTON_LABEL_LIMIT, variants=True,
    ),
    Field(
        "SELECT_PREVIOUS_WEEK", Kind.BUTTON, Group.MENU,
        "Кнопка «За минулі тижні»",
        "Показує список тижнів, щоб заповнити пропущений.",
        limit=BUTTON_LABEL_LIMIT, variants=True,
    ),
    Field(
        "GET_MY_STATS", Kind.BUTTON, Group.MENU,
        "Кнопка «Моя статистика»",
        "Показує суму відповідей за весь семестр.",
        limit=BUTTON_LABEL_LIMIT, variants=True,
    ),

    # --------------------------------------------------------- registration --
    Field(
        "ASK_NAME", Kind.TEXT, Group.REGISTRATION,
        "Запит імені",
        "Перше, що бот пише новому користувачеві. Він має надіслати своє "
        "прізвище та ім'я так, як вони записані в таблиці волонтерів.",
        required=False,
    ),
    Field(
        "REGISTRATION_COMPLETE", Kind.TEMPLATE, Group.REGISTRATION,
        "Реєстрація завершена",
        "Надсилається після успішного розпізнавання імені. {0} — ім'я волонтера.",
        placeholders=("{0}",), variants=True,
    ),
    Field(
        "NOT_REGISTERED", Kind.TEXT, Group.REGISTRATION,
        "Ще не зареєстрований",
        "Відповідь тому, хто пише боту, не завершивши реєстрацію.",
    ),
    Field(
        "GREETING_REGISTERED", Kind.TEMPLATE, Group.REGISTRATION,
        "Привітання зареєстрованого",
        "Коротке вітання на /start для того, хто вже зареєстрований. "
        "{0} — ім'я. Не плутати з GREETING, що є загальним описом бота.",
        placeholders=("{0}",), required=False,
    ),
    Field(
        "NAME_NOT_RECOGNISED", Kind.TEXT, Group.REGISTRATION,
        "Імені не знайдено",
        "Коли надіслане ім'я не збігається з жодним волонтером. Текст "
        "однаковий для «не знайдено», «вже зареєстрований» і «неоднозначно» — "
        "інакше можна було б по одному перебрати весь список волонтерів.",
        required=False,
    ),

    # ------------------------------------------------------------ reminders --
    Field(
        "REMINDER1", Kind.TEMPLATE, Group.REMINDERS,
        "Нагадування 1 (17:00)",
        "Перше нагадування щонеділі, тим, у кого рядок за цей тиждень ще "
        "порожній. {0} — ім'я.",
        placeholders=("{0}",), variants=True, dynamic=True, html=True,
    ),
    Field(
        "REMINDER2", Kind.TEMPLATE, Group.REMINDERS,
        "Нагадування 2 (19:00)",
        "Друге нагадування, якщо статистики досі немає. {0} — ім'я.",
        placeholders=("{0}",), variants=True, dynamic=True, html=True,
    ),
    Field(
        "REMINDER3", Kind.TEMPLATE, Group.REMINDERS,
        "Нагадування 3 (21:00)",
        "Останнє нагадування за тиждень. {0} — ім'я.",
        placeholders=("{0}",), variants=True, dynamic=True, html=True,
    ),

    # -------------------------------------------------------------- service --
    Field(
        "SELECT_WEEK", Kind.TEXT, Group.SERVICE,
        "Вибір тижня",
        "Підпис над списком тижнів.",
    ),
    Field(
        "SELECTED_WEEK", Kind.TEXT, Group.SERVICE,
        "Обраний тиждень",
        "Підтвердження вибору. Бот додає номер тижня й дати після цього тексту.",
    ),
    Field(
        "STATISTICS_GATHERED", Kind.TEXT, Group.SERVICE,
        "Статистику збережено",
        "Після останнього питання.",
        variants=True,
    ),
    Field(
        "NO_STATS_AVAILABLE", Kind.TEXT, Group.SERVICE,
        "Статистики ще немає",
        "Коли волонтер просить свою статистику, а жодної відповіді ще не "
        "збережено.",
        required=False,
    ),
    Field(
        "VALIDATION_FAILED_NUMERIC", Kind.TEXT, Group.SERVICE,
        "Відповідь не число",
        "Коли замість числа надіслано щось інше. Бот повторює те саме питання.",
        required=False,
    ),
    Field(
        "BOT_UNAVAILABLE", Kind.TEMPLATE, Group.SERVICE,
        "Бот недоступний",
        "Поза межами семестру, коли ще не початок і вже не кінець. "
        "{0} — ім'я, якщо бот його знає.",
        variants=True,
    ),
    Field(
        "SEMESTER_NOT_STARTED", Kind.TEMPLATE, Group.SERVICE,
        "Семестр ще не почався",
        "До START_DATE. {0} — ім'я, якщо бот його знає.",
        placeholders=("{0}",), variants=True,
    ),
    Field(
        "SEMESTER_OVER", Kind.TEMPLATE, Group.SERVICE,
        "Семестр завершено",
        "Надсилається після END_DATE замість будь-якої іншої відповіді. "
        "Збір статистики на цей семестр закрито.",
        variants=True,
    ),
    Field(
        "STATS_HEADER", Kind.TEXT, Group.SERVICE,
        "Заголовок статистики",
        "Рядок над підсумковою таблицею.",
        required=False, limit=200,
    ),
    Field(
        "NO_STATS_TEXT", Kind.TEXT, Group.SERVICE,
        "Таблиця порожня",
        "Замість таблиці, коли сумувати нічого.",
        required=False, limit=200,
    ),
    Field(
        "WEEK_LABEL", Kind.TEXT, Group.SERVICE,
        "Слово «тиждень»",
        "Префікс у кнопках вибору тижня.",
        required=False, limit=40,
    ),
    Field(
        "WEEK_NOT_FILLED", Kind.TEXT, Group.SERVICE,
        "«не заповнено»",
        "Підпис тижня без відповідей у списку вибору.",
        required=False, limit=40,
    ),
    Field(
        "CANCELLED", Kind.TEXT, Group.SERVICE,
        "Скасовано",
        "Відповідь на /cancel під час заповнення.",
        required=False, limit=200,
    ),
    Field(
        "NOTHING_TO_CANCEL", Kind.TEXT, Group.SERVICE,
        "Нічого скасовувати",
        "Відповідь на /cancel, коли заповнення не починалося.",
        required=False, limit=200,
    ),
    Field(
        "UNREGISTERED", Kind.TEXT, Group.SERVICE,
        "Реєстрацію скасовано",
        "Після /unregister_me.",
        required=False,
    ),
    Field(
        "UNKNOWN_COMMAND", Kind.TEXT, Group.SERVICE,
        "Невідома команда",
        "Відповідь зареєстрованому волонтеру на команду, якої бот не знає.",
        required=False, limit=200,
    ),
    Field(
        "SESSION_LOST", Kind.TEXT, Group.SERVICE,
        "Сесію втрачено",
        "Коли бот перезапустився посеред заповнення й втратив, за який тиждень "
        "йшла мова. Заповнення треба почати спочатку.",
        required=False,
    ),

    # --------------------------------------------------------------- unused --
    # Present in the sheet, read by nothing. Kept described so the UI can show
    # them as deletable rather than leaving a reader to guess.
    Field("GREETING", Kind.TEXT, Group.UNUSED, "Опис бота",
          "Загальний опис бота. Раніше показувався на /start.", required=False),
    Field("WHY_I_AM_HERE", Kind.TEXT, Group.UNUSED, "«Чому я тут»",
          "Пояснення призначення бота.", required=False),
    Field("WHAT_I_DO", Kind.TEXT, Group.UNUSED, "«Що я роблю»",
          "Опис щотижневого процесу.", required=False),
    Field("MENU", Kind.TEXT, Group.UNUSED, "Список команд",
          "Перелік команд. Телеграм показує власне меню команд.", required=False),
    Field("SEND_STATISTICS", Kind.BUTTON, Group.UNUSED, "Кнопка «Надіслати»",
          "Колишня кнопка надсилання.", required=False, limit=BUTTON_LABEL_LIMIT),
    Field("YES", Kind.BUTTON, Group.UNUSED, "«Так»",
          "Колишня кнопка підтвердження.", required=False, limit=BUTTON_LABEL_LIMIT),
    Field("NO", Kind.BUTTON, Group.UNUSED, "«Ні»",
          "Колишня кнопка відмови.", required=False, limit=BUTTON_LABEL_LIMIT),
    Field("WRITING", Kind.TEXT, Group.UNUSED, "«Записую…»",
          "Підтвердження після кожної відповіді. Бот його не надсилає.",
          required=False, variants=True, limit=200),
    Field("GETTING_YOUR_STATISTICS", Kind.TEXT, Group.UNUSED, "«Дивлюся статистику…»",
          "Повідомлення перед показом статистики.", required=False, variants=True),
    Field("VALIDATION_FAILED_NAME", Kind.TEMPLATE, Group.UNUSED, "Ім'я не підходить",
          "{0} — звертання. Не використовується: на цьому кроці бот ще не знає, "
          "з ким говорить.", placeholders=("{0}",), required=False),
    Field("REMINDER_LATE", Kind.TEMPLATE, Group.UNUSED, "Пізнє нагадування",
          "{0} — ім'я. Колишнє окреме нагадування.",
          placeholders=("{0}",), required=False),
    Field("REMINDER_SORRY", Kind.TEXT, Group.UNUSED, "Вибачення за пізнє нагадування",
          "Колишнє доповнення до пізнього нагадування.", required=False),
    Field("SELECT_YOUR_NAME", Kind.TEXT, Group.UNUSED, "«Обери себе зі списку»",
          "Застаріле: списку волонтерів більше немає, бот просить написати ім'я. "
          "Див. ASK_NAME.", required=False),
)

BY_KEY: dict[str, Field] = {f.key: f for f in FIELDS}

#: Keys whose value is a ';;'-separated list of interchangeable variants.
VARIANT_KEYS: frozenset[str] = frozenset(f.key for f in FIELDS if f.variants)

#: Keys the bot cannot start without, ignoring the conditional question family.
REQUIRED_KEYS: tuple[str, ...] = tuple(
    f.key for f in FIELDS if f.required and not f.conditional
)


def question_keys(pattern: str) -> tuple[str, ...]:
    """Question keys a given schedule actually needs."""
    if not pattern or not pattern.isdigit():
        return ()
    most = max(int(digit) for digit in pattern)
    return tuple(
        key
        for n in range(1, most + 1)
        for key in (f"QUESTION_{n}", f"QUESTION_{n}_NAME")
    )


def fields_in_group(group: Group) -> tuple[Field, ...]:
    return tuple(f for f in FIELDS if f.group is group)
