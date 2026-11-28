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
