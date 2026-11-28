"""Checks run before anything is written.

A problem is either an error, which blocks the save, or a warning, which is
shown and then obeyed if the editor insists. The split exists because some
things are certainly wrong -- a date that does not parse -- and some are only
probably wrong, and a tool that refuses the second kind gets worked around.

Messages are in English even though the pages are Ukrainian: a problem here is
about the configuration rather than about the bot's words, and English is what
the rest of the project's operational text uses.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from string import Formatter

from bot.lexicon import SINGLE_VALUE_KINDS
from bot.registry import MAX_QUESTIONS, TELEGRAM_MESSAGE_LIMIT, Field, Kind

#: Telegram's own rule: 5-32 characters, Latin letters, digits and
#: underscores, starting with a letter. Checked here so an unusable invitation
#: is refused at the form rather than sitting in the table forever waiting for
#: a login that can never match it.
USERNAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{4,31}$")


@dataclass(frozen=True, slots=True)
class Problem:
    level: str  # "error" blocks the save, "warning" does not
    message: str

    @property
    def blocking(self) -> bool:
        return self.level == "error"


def error(message: str) -> Problem:
    return Problem("error", message)


def warning(message: str) -> Problem:
    return Problem("warning", message)


def blocking(problems: list[Problem]) -> bool:
    return any(problem.blocking for problem in problems)


def check_username(
    handle: str, *, already_admin: bool = False, already_invited: bool = False
) -> list[Problem]:
    """Problems with a username being invited to edit the configuration.

    The handle is expected normalised (no ``@``, no ``t.me/``, casefolded), so
    what is checked here is exactly what will be compared against the login
    payload.
    """
    if not handle:
        return [error("Enter a Telegram username, for example @olha_k.")]
    if not USERNAME_PATTERN.match(handle):
        return [
            error(
                "A Telegram username is 5–32 Latin letters, digits and "
                "underscores, starting with a letter. Check that this is the "
                "username and not the display name."
            )
        ]
    if already_admin:
        return [error(f"@{handle} already has access.")]
    if already_invited:
        return [
            error(f"@{handle} is already invited — the invitation is waiting "
                  "for their first sign-in.")
        ]
    return []


# ------------------------------------------------------------ placeholders --

#: Telegram's allowed tag set for parse_mode="HTML". Anything else makes the
#: API reject the whole message, so the reminder simply never arrives.
TELEGRAM_TAGS = frozenset(
    {
        "b", "strong", "i", "em", "u", "ins", "s", "strike", "del",
        "span", "tg-spoiler", "a", "code", "pre", "blockquote", "tg-emoji",
    }
)

#: Tags that carry no closing partner in practice. Telegram has none, but
#: keeping the set explicit means the balance check reads as intentional.
VOID_TAGS: frozenset[str] = frozenset()

_TAG = re.compile(r"<\s*(/?)\s*([a-zA-Z][a-zA-Z0-9-]*)[^>]*>")
_BARE_LT = re.compile(r"<(?!\s*/?\s*[a-zA-Z])")
_BARE_AMP = re.compile(r"&(?!#?\w+;)")


def placeholders_in(value: str) -> list[str]:
    """Format fields the value actually contains, in order.

    ``str.format`` is what the bot calls, so this parses with the same
    machinery rather than a regex: ``{{`` is an escaped brace and must not be
    reported.
    """
    found = []
    for _, name, _, _ in Formatter().parse(value):
        if name is not None:
            found.append("{" + name + "}")
    return found


def check_placeholders(field: Field, value: str) -> list[Problem]:
    """Placeholder problems, graded by what the bot actually does.

    ``fill()`` catches IndexError, KeyError and ValueError and sends the
    template unsubstituted, so none of these crash the bot -- they decide
    whether a volunteer is shown a literal ``{1}``.
    """
    try:
        present = placeholders_in(value)
    except ValueError:
        return [
            error(
                "Unbalanced curly brace. To show a brace as a character, "
                "double it: {{ or }}."
            )
        ]

    problems: list[Problem] = []
    allowed = set(field.placeholders)

    for found in present:
        if found not in allowed:
            problems.append(
                error(
                    f"{found} is not something the bot substitutes here"
                    + (
                        f" — it only fills in {', '.join(field.placeholders)}."
                        if field.placeholders
                        else ", and this message gets no values at all."
                    )
                    + " The message would be sent with the braces showing."
                )
            )

    # A declared placeholder that is missing is a warning, not an error: the
    # bot calls str.format, which ignores an argument the template does not
    # use. Several live reminders deliberately leave the name out.
    for wanted in field.placeholders:
        if wanted not in present:
            problems.append(
                warning(
                    f"{wanted} is missing, so the value the bot would put there "
                    "will not appear. That is fine if the wording does not need it."
                )
            )
    return problems


# -------------------------------------------------------------------- HTML --


def check_telegram_html(value: str) -> list[Problem]:
    """Markup problems for a value sent with parse_mode="HTML".

    Telegram rejects the whole message when the markup does not parse, and a
    rejected reminder is a reminder nobody receives -- visible only in the log.
    """
    problems: list[Problem] = []

    if _BARE_LT.search(value):
        problems.append(
            error(
                "A '<' that does not open a tag. Telegram would refuse the whole "
                "message, so nobody would get it. Write it as &lt; instead."
            )
        )

    open_tags: list[str] = []
    for closing, name in _TAG.findall(value):
        name = name.lower()
        if name not in TELEGRAM_TAGS:
            problems.append(
                error(
                    f"<{name}> is not a tag Telegram understands. Allowed here: "
                    f"{', '.join(sorted(TELEGRAM_TAGS))}."
                )
            )
            continue
        if name in VOID_TAGS:
            continue
        if closing:
            if not open_tags:
                problems.append(error(f"</{name}> closes a tag that was never opened."))
            elif open_tags[-1] != name:
                problems.append(
                    error(
                        f"</{name}> closes out of order — <{open_tags[-1]}> is still "
                        "open. Telegram would refuse the message."
                    )
                )
                open_tags.pop()
            else:
                open_tags.pop()
        else:
            open_tags.append(name)

    for name in open_tags:
        problems.append(
            error(f"<{name}> is never closed. Add </{name}>.")
        )

    if _BARE_AMP.search(value):
        problems.append(
            warning(
                "A bare '&'. Telegram asks for &amp; in HTML messages; it usually "
                "goes through, but write &amp; to be certain."
            )
        )
    return problems


# ------------------------------------------------------------- whole fields --


def check_variants(
    field: Field,
    values: list[str],
    *,
    other_labels: dict[str, list[str]] | None = None,
    question_pattern: str | None = None,
    other_date: str | None = None,
) -> list[Problem]:
    """Every problem with a proposed value for one key.

    ``other_labels`` maps every *other* button key to its labels, so a label
    that would shadow another button is caught. ``question_pattern`` is the
    live pattern, needed to say how many question keys a new one would require.
    ``other_date`` is the opposite end of the semester.
    """
    values = [v.strip() for v in values if v.strip()]

    if not values:
        if field.required and not field.conditional:
            return [error("This value cannot be empty — the bot will not start without it.")]
        return [error("This value cannot be empty. Delete the key instead to fall back.")]

    problems: list[Problem] = []

    if field.kind in SINGLE_VALUE_KINDS and len(values) > 1:
        problems.append(
            error(
                f"{field.key} is read as a value, not shown as a message, so it "
                "takes exactly one phrasing."
            )
        )

    for value in values:
        if len(value) > field.limit:
            problems.append(
                error(
                    f"{len(value)} characters, and the limit here is {field.limit}."
                    + (
                        " Telegram refuses a longer message."
                        if field.limit == TELEGRAM_MESSAGE_LIMIT
                        else ""
                    )
                )
            )
        problems.extend(check_placeholders(field, value))
        if field.html:
            problems.extend(check_telegram_html(value))

    if field.kind is Kind.DATE:
        problems.extend(_check_date(field, values[0], other_date))
    elif field.kind is Kind.PATTERN:
        problems.extend(_check_pattern(values[0]))
    elif field.kind is Kind.BUTTON:
        problems.extend(_check_button(field, values, other_labels or {}))
    elif field.kind is Kind.WORKSHEET:
        problems.append(
            warning(
                "This must match the tab name in the data spreadsheet exactly, "
                "including spaces. A tab that does not exist means every read and "
                "write to the sheet fails."
            )
        )

    if question_pattern and field.kind is Kind.PATTERN:
        pass  # the pattern's own checks cover it; kept for symmetry of callers

    return problems


def _check_date(field: Field, value: str, other: str | None) -> list[Problem]:
    try:
        parsed = datetime.strptime(value, "%d.%m.%Y")
    except ValueError:
        return [error("Write the date as dd.mm.yyyy, for example 07.09.2026.")]

    if other is None:
        return []
    try:
        other_parsed = datetime.strptime(other, "%d.%m.%Y")
    except ValueError:
        # The other end is already broken; saying so twice does not help.
        return []

    start, end = (
        (parsed, other_parsed) if field.key == "START_DATE" else (other_parsed, parsed)
    )
    problems: list[Problem] = []
    if end < start:
        problems.append(error("The semester would end before it starts."))
        return problems
    if start.year != end.year:
        problems.append(
            error(
                "The semester must fall inside one calendar year. Every week "
                "calculation uses bare ISO week numbers, so one spanning New Year "
                "would make the bot permanently unavailable."
            )
        )
        return problems
    if start.isocalendar()[1] > end.isocalendar()[1]:
        problems.append(error("The start date falls in a later ISO week than the end date."))
    return problems


def _check_pattern(value: str) -> list[Problem]:
    if not value.isdigit():
        return [
            error(
                "The pattern is one digit per week — how many questions that week "
                "asks — repeating until the semester ends. For example 3232 asks "
                "three, then two, then three again."
            )
        ]
    if set(value) == {"0"}:
        return [error("Every week is 0, so no week would ask anything.")]

    problems: list[Problem] = []
    most = max(int(d) for d in value)
    if most > MAX_QUESTIONS:
        problems.append(
            error(
                f"A week would ask {most} questions, and the bot is described for "
                f"at most {MAX_QUESTIONS}."
            )
        )
    if "0" in value:
        problems.append(
            warning("Weeks marked 0 ask nothing, and the bot stays quiet on them.")
        )
    problems.append(
        warning(
            "Changing this moves which spreadsheet columns a week's answers live "
            "in. Check the sheet after saving."
        )
    )
    return problems


def _check_button(
    field: Field, values: list[str], other_labels: dict[str, list[str]]
) -> list[Problem]:
    problems: list[Problem] = []
    for value in values:
        if value.startswith("/"):
            problems.append(
                error(
                    "A label starting with '/' looks like a command to Telegram, "
                    "and the handler that reads answers skips commands, so this "
                    "button would do nothing."
                )
            )
        for other_key, labels in other_labels.items():
            if other_key != field.key and value in labels:
                problems.append(
                    error(
                        f"'{value}' is already the label of {other_key}. The bot "
                        "matches an incoming message against every label, so "
                        "whichever handler is registered first would answer both."
                    )
                )
    return problems
