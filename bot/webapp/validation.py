"""Checks run before anything is written.

A problem is either an error, which blocks the save, or a warning, which is
shown and then obeyed if the editor insists. The split exists because some
things are certainly wrong -- a date that does not parse -- and some are only
probably wrong, and a tool that refuses the second kind gets worked around.
"""

import re
from dataclasses import dataclass

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
        return [error("Вкажіть нік у Telegram, наприклад @olha_k.")]
    if not USERNAME_PATTERN.match(handle):
        return [
            error(
                "Нік у Telegram складається з 5–32 латинських літер, цифр і "
                "підкреслень та починається з літери. Перевірте, чи це справді "
                "нік, а не ім’я."
            )
        ]
    if already_admin:
        return [error(f"@{handle} уже має доступ.")]
    if already_invited:
        return [error(f"@{handle} уже запрошено. Запрошення чекає на перший вхід.")]
    return []
