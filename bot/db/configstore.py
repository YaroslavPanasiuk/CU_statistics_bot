"""Postgres store for the bot's configuration.

The bot reads; the configuration web UI writes. They are separate processes
sharing only this database, so neither needs to know the other is running.

Three things are deliberate.

**Variants are rows, not a ``;;``-joined cell.** Twenty-one keys hold several
interchangeable phrasings and one is picked at random. Packed into a single
cell, adding a variant meant editing a long line by hand -- and it silently
broke ``LexiconFilter``, which compared an incoming message against the whole
packed string, so a button stopped matching its own handler the moment a second
variant was added.

**``config_version`` is bumped on every write.** The bot polls it rather than
being signalled, which keeps the two processes decoupled. An edit reaches a
running bot within one poll interval, with no restart and no Telegram command.

**Every write is audited.** The spreadsheet could not say who changed a message
or when. ``config_audit`` keeps the old and new values, the key, the timestamp
and the editor.

Meaning lives in :mod:`bot.registry`; only values live here.
"""

import json
import logging
from datetime import datetime, timedelta
from typing import ClassVar

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    delete,
    func,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column

from bot.db.database import Base, Session

logger = logging.getLogger(__name__)

#: How long an admin invitation stays usable. Telegram usernames can be
#: released and re-registered by someone else, so an invite that never expired
#: would eventually hand admin rights to a stranger who claimed the handle.
INVITE_TTL = timedelta(days=7)

VERSION_KEY = "config_version"


class Setting(Base):
    """One configuration key. Its values hang off it as variant rows."""

    __tablename__ = "settings"
    __table_args__: ClassVar[dict] = {
        "comment": "One row per configuration key; its values hang off setting_variants."
    }

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    updated_by: Mapped[str] = mapped_column(String(100), default="")


class SettingVariant(Base):
    """One of a key's interchangeable phrasings, in display order."""

    __tablename__ = "setting_variants"
    __table_args__ = (
        UniqueConstraint("key", "position", name="_setting_variant_uc"),
        {"comment": "Interchangeable phrasings for a key, in display order."},
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(
        ForeignKey("settings.key", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    value: Mapped[str] = mapped_column(Text)


class ConfigMeta(Base):
    """Small key/value scratchpad; holds the version counter."""

    __tablename__ = "config_meta"
    __table_args__: ClassVar[dict] = {
        "comment": "Holds config_version, which the bot polls to pick up edits."
    }

    name: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class ConfigAudit(Base):
    """Who changed which key, when, and to what."""

    __tablename__ = "config_audit"
    __table_args__: ClassVar[dict] = {
        "comment": "Who changed which key, when, and to what. The sheet could not say."
    }

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    changed_by: Mapped[str] = mapped_column(String(100))
    key: Mapped[str] = mapped_column(String(100), index=True)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)


class Admin(Base):
    """An admin granted access from the web UI.

    The ids in ``ADMIN_IDS`` are deliberately *not* copied here: the .env file
    stays the way back in if this table is ever emptied by mistake.
    """

    __tablename__ = "admins"
    __table_args__: ClassVar[dict] = {
        "comment": (
            "Admins granted from the web UI. ADMIN_IDS from .env is deliberately "
            "not copied here: that stays the way back in if this table is emptied."
        )
    }

    # A Telegram user id, supplied by the login payload. autoincrement=False
    # keeps Postgres from putting a sequence default on it.
    tg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str] = mapped_column(String(100), default="")
    first_name: Mapped[str] = mapped_column(String(100), default="")
    added_by: Mapped[str] = mapped_column(String(100))
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AdminInvite(Base):
    """A username invited before anyone knows their numeric id.

    A bot cannot resolve ``@someone`` to an id -- the Bot API has no such call,
    deliberately -- so the id is captured from the Telegram login payload the
    first time they sign in, and the invite is consumed at that moment.
    """

    __tablename__ = "admin_invites"
    __table_args__: ClassVar[dict] = {
        "comment": (
            "A username invited before anyone knows their numeric id. The Bot API "
            "cannot resolve @someone to an id, so it is captured from the Telegram "
            "login payload on first sign-in and the invite consumed there."
        )
    }

    username: Mapped[str] = mapped_column(String(100), primary_key=True)
    invited_by: Mapped[str] = mapped_column(String(100))
    invited_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def normalise_username(raw: str) -> str:
    """A Telegram username reduced to its comparable form.

    People paste ``@name``, ``t.me/name`` and ``https://t.me/name``, and
    Telegram itself treats usernames case-insensitively. One canonical form is
    what makes "already invited" and "matches this login" answerable at all:
    an invitation stored as ``t.me/olha`` would never match a login payload's
    ``olha``, and the person would simply never get in with nothing to explain
    why.
    """
    text = (raw or "").strip()
    for prefix in ("https://", "http://"):
        if text.lower().startswith(prefix):
            text = text[len(prefix) :]
    for prefix in ("t.me/", "telegram.me/", "telegram.dog/"):
        if text.lower().startswith(prefix):
            text = text[len(prefix) :]
    return text.lstrip("@").strip().casefold()


# --------------------------------------------------------------- reading --


async def load() -> dict[str, list[str]]:
    """Every key with its variants in order."""
    async with Session() as session:
        rows = await session.execute(
            select(SettingVariant.key, SettingVariant.value).order_by(
                SettingVariant.key, SettingVariant.position
            )
        )
        values: dict[str, list[str]] = {}
        for key, value in rows.all():
            values.setdefault(key, []).append(value)
        return values


async def version() -> int:
    """The counter the bot polls. 0 when nothing has been written yet."""
    async with Session() as session:
        raw = await session.scalar(
            select(ConfigMeta.value).where(ConfigMeta.name == VERSION_KEY)
        )
        return int(raw) if raw else 0


async def is_populated() -> bool:
    async with Session() as session:
        return bool(await session.scalar(select(func.count()).select_from(Setting)))


async def audit(limit: int = 100) -> list[ConfigAudit]:
    async with Session() as session:
        rows = await session.execute(
            select(ConfigAudit).order_by(ConfigAudit.changed_at.desc()).limit(limit)
        )
        return list(rows.scalars().all())


# --------------------------------------------------------------- writing --


async def _bump_version(session) -> None:
    stmt = insert(ConfigMeta).values(name=VERSION_KEY, value="1")
    stmt = stmt.on_conflict_do_update(
        index_elements=["name"],
        set_={"value": (func.cast(ConfigMeta.value, Integer) + 1).cast(Text)},
    )
    await session.execute(stmt)


async def set_variants(key: str, values: list[str], changed_by: str) -> None:
    """Replace a key's variants, bump the version, record the change.

    An empty list is rejected: a key with no value is indistinguishable from a
    missing key, and a missing required key stops the bot from starting.
    """
    cleaned = [v for v in (value.strip() for value in values) if v]
    if not cleaned:
        raise ValueError(f"{key} would have no value; delete the key instead")

    async with Session() as session:
        previous = await session.execute(
            select(SettingVariant.value)
            .where(SettingVariant.key == key)
            .order_by(SettingVariant.position)
        )
        old = list(previous.scalars().all())

        await session.execute(
            insert(Setting)
            .values(key=key, updated_by=changed_by)
            .on_conflict_do_update(
                index_elements=["key"],
                set_={"updated_by": changed_by, "updated_at": func.now()},
            )
        )
        await session.execute(delete(SettingVariant).where(SettingVariant.key == key))
        session.add_all(
            SettingVariant(key=key, position=i, value=value)
            for i, value in enumerate(cleaned)
        )
        session.add(
            ConfigAudit(
                changed_by=changed_by,
                key=key,
                old_value=json.dumps(old, ensure_ascii=False) if old else None,
                new_value=json.dumps(cleaned, ensure_ascii=False),
            )
        )
        await _bump_version(session)
        await session.commit()


async def delete_key(key: str, changed_by: str) -> bool:
    async with Session() as session:
        previous = await session.execute(
            select(SettingVariant.value)
            .where(SettingVariant.key == key)
            .order_by(SettingVariant.position)
        )
        old = list(previous.scalars().all())
        result = await session.execute(delete(Setting).where(Setting.key == key))
        if result.rowcount:
            session.add(
                ConfigAudit(
                    changed_by=changed_by,
                    key=key,
                    old_value=json.dumps(old, ensure_ascii=False) if old else None,
                    new_value=None,
                )
            )
            await _bump_version(session)
        await session.commit()
        return bool(result.rowcount)


async def import_mapping(mapping: dict[str, list[str]], changed_by: str) -> int:
    """Bulk-load keys, for the one-time import from the config spreadsheet.

    Idempotent: re-running replaces what is there. One version bump for the
    whole import rather than one per key.
    """
    written = 0
    async with Session() as session:
        for key, values in mapping.items():
            cleaned = [v for v in (value.strip() for value in values) if v]
            if not cleaned:
                continue
            await session.execute(
                insert(Setting)
                .values(key=key, updated_by=changed_by)
                .on_conflict_do_update(
                    index_elements=["key"],
                    set_={"updated_by": changed_by, "updated_at": func.now()},
                )
            )
            await session.execute(delete(SettingVariant).where(SettingVariant.key == key))
            session.add_all(
                SettingVariant(key=key, position=i, value=value)
                for i, value in enumerate(cleaned)
            )
            written += 1
        session.add(
            ConfigAudit(
                changed_by=changed_by,
                key="(import)",
                old_value=None,
                new_value=json.dumps({"keys": written}, ensure_ascii=False),
            )
        )
        await _bump_version(session)
        await session.commit()
    return written


# ---------------------------------------------------------------- admins --


async def admin_ids() -> set[int]:
    """Admins granted from the web UI. The .env seed is added by the caller."""
    async with Session() as session:
        rows = await session.execute(select(Admin.tg_id))
        return set(rows.scalars().all())


async def admins() -> list[Admin]:
    async with Session() as session:
        rows = await session.execute(select(Admin).order_by(Admin.added_at))
        return list(rows.scalars().all())


async def add_admin(
    tg_id: int, username: str, first_name: str, added_by: str
) -> None:
    async with Session() as session:
        await session.execute(
            insert(Admin)
            .values(
                tg_id=tg_id,
                username=normalise_username(username),
                first_name=first_name,
                added_by=added_by,
            )
            .on_conflict_do_update(
                index_elements=["tg_id"],
                set_={"username": normalise_username(username), "first_name": first_name},
            )
        )
        session.add(
            ConfigAudit(
                changed_by=added_by,
                key="(admin)",
                old_value=None,
                new_value=json.dumps({"granted": tg_id, "username": username}, ensure_ascii=False),
            )
        )
        await _bump_version(session)
        await session.commit()


async def remove_admin(tg_id: int, removed_by: str) -> bool:
    async with Session() as session:
        result = await session.execute(delete(Admin).where(Admin.tg_id == tg_id))
        if result.rowcount:
            session.add(
                ConfigAudit(
                    changed_by=removed_by,
                    key="(admin)",
                    old_value=json.dumps({"revoked": tg_id}, ensure_ascii=False),
                    new_value=None,
                )
            )
            await _bump_version(session)
        await session.commit()
        return bool(result.rowcount)


async def invite_admin(username: str, invited_by: str) -> str:
    """Record an invitation for a username whose numeric id is unknown."""
    handle = normalise_username(username)
    if not handle:
        raise ValueError("a username is required")

    async with Session() as session:
        await session.execute(
            insert(AdminInvite)
            .values(
                username=handle,
                invited_by=invited_by,
                expires_at=datetime.now().astimezone() + INVITE_TTL,
            )
            .on_conflict_do_update(
                index_elements=["username"],
                set_={
                    "invited_by": invited_by,
                    "invited_at": func.now(),
                    "expires_at": datetime.now().astimezone() + INVITE_TTL,
                },
            )
        )
        session.add(
            ConfigAudit(
                changed_by=invited_by,
                key="(admin invite)",
                old_value=None,
                new_value=json.dumps({"invited": handle}, ensure_ascii=False),
            )
        )
        await session.commit()
    return handle


async def pending_invites() -> list[AdminInvite]:
    async with Session() as session:
        rows = await session.execute(
            select(AdminInvite).order_by(AdminInvite.invited_at)
        )
        return list(rows.scalars().all())


async def revoke_invite(username: str, revoked_by: str) -> bool:
    handle = normalise_username(username)
    async with Session() as session:
        result = await session.execute(
            delete(AdminInvite).where(AdminInvite.username == handle)
        )
        if result.rowcount:
            session.add(
                ConfigAudit(
                    changed_by=revoked_by,
                    key="(admin invite)",
                    old_value=json.dumps({"revoked_invite": handle}, ensure_ascii=False),
                    new_value=None,
                )
            )
        await session.commit()
        return bool(result.rowcount)


async def consume_invite(username: str, tg_id: int, first_name: str) -> bool:
    """Turn a live invitation into an admin, keyed on the numeric id.

    Called from the login flow, which is the only place a username and an id
    arrive together. Expired invitations are removed rather than honoured.
    """
    handle = normalise_username(username)
    if not handle:
        return False

    async with Session() as session:
        invite = await session.scalar(
            select(AdminInvite).where(AdminInvite.username == handle)
        )
        if invite is None:
            return False

        expired = invite.expires_at < datetime.now().astimezone()
        await session.execute(delete(AdminInvite).where(AdminInvite.username == handle))
        if expired:
            session.add(
                ConfigAudit(
                    changed_by=handle,
                    key="(admin invite)",
                    old_value=json.dumps({"expired": handle}, ensure_ascii=False),
                    new_value=None,
                )
            )
            await session.commit()
            logger.warning(f"Admin invitation for @{handle} had expired; not granting")
            return False

        await session.execute(
            insert(Admin)
            .values(
                tg_id=tg_id,
                username=handle,
                first_name=first_name,
                added_by=invite.invited_by,
            )
            .on_conflict_do_update(
                index_elements=["tg_id"],
                set_={"username": handle, "first_name": first_name},
            )
        )
        session.add(
            ConfigAudit(
                changed_by=invite.invited_by,
                key="(admin)",
                old_value=None,
                new_value=json.dumps(
                    {"granted": tg_id, "username": handle, "via": "invite"},
                    ensure_ascii=False,
                ),
            )
        )
        await _bump_version(session)
        await session.commit()
        return True


async def update_admin_identity(tg_id: int, username: str, first_name: str) -> None:
    """Keep the display fields current; the id is the identity."""
    async with Session() as session:
        await session.execute(
            update(Admin)
            .where(Admin.tg_id == tg_id)
            .values(username=normalise_username(username), first_name=first_name)
        )
        await session.commit()
