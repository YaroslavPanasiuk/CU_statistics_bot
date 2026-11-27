import asyncio
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Table,
    UniqueConstraint,
    asc,
    delete,
    func,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from bot.config import config

engine = create_async_engine(config.DATABASE_URL)
Session = async_sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "volunteers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    full_name: Mapped[str] = mapped_column(unique=True)
    tg_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, nullable=True)


class UserStats(Base):
    __tablename__ = "statistics"

    __table_args__ = (
        UniqueConstraint("volunteer_id", "week", name="_volunteer_week_uc"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # Owned by the volunteer's primary key rather than their tg_id. tg_id is
    # nullable -- unregistering clears it -- and recyclable, so keying on it
    # both orphaned rows on unregister and let a new holder of a tg_id inherit
    # someone else's history. See migrations/versions/0002.
    volunteer_id: Mapped[int] = mapped_column(
        ForeignKey("volunteers.id", ondelete="CASCADE")
    )
    week: Mapped[int] = mapped_column()
    stats: Mapped[dict] = mapped_column(JSONB, default=dict)
    last_modified: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now()
    )


# Statistics whose volunteer row was deleted back when no foreign key existed
# (see migrations/versions/0002). Declared here only so the model and the
# database stay in step -- without it `alembic revision --autogenerate` would
# cheerfully propose dropping the table. Nothing in the bot reads it.
statistics_archive = Table(
    "statistics_archive",
    Base.metadata,
    Column("id", Integer, primary_key=True, autoincrement=False),
    Column("tg_id", BigInteger, nullable=False),
    Column("week", Integer, nullable=False),
    Column("stats", JSONB, nullable=False),
    Column("last_modified", DateTime(timezone=True), nullable=False),
    Column("archived_at", DateTime(timezone=True), server_default=text("now()"), nullable=False),
    comment=(
        "Statistics whose volunteer was deleted before this migration; "
        "retained for historical totals."
    ),
)


class WeekStats(NamedTuple):
    """One volunteer-week of answers.

    Named so callers stop indexing raw Rows positionally (`stats[0][1]`),
    which silently broke whenever a select()'s column order changed.
    """

    full_name: str
    stats: dict
    week: int
    last_modified: datetime | None = None


async def init_db():
    """Bring the schema up to head.

    Replaces create_all(), which only ever created *missing* tables and so let
    the model and the database drift apart -- the reason production ran for
    months without the foreign key the model declared. Alembic's env.py drives
    its own event loop, so this runs in a worker thread.
    """
    from alembic import command
    from alembic.config import Config as AlembicConfig

    def upgrade() -> None:
        root = Path(__file__).resolve().parents[2]
        cfg = AlembicConfig(str(root / "alembic.ini"))
        cfg.set_main_option("script_location", str(root / "migrations"))
        command.upgrade(cfg, "head")

    await asyncio.to_thread(upgrade)


async def dispose_db():
    await engine.dispose()


async def get_unregistered_users():
    async with Session() as session:
        stmt = select(User).where(User.tg_id.is_(None)).order_by(asc(User.id))
        result = await session.execute(stmt)
        return result.scalars().all()


async def get_user_by_tg_id(tg_id: int) -> User | None:
    async with Session() as session:
        stmt = select(User).where(User.tg_id == tg_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


async def get_user_by_full_name(full_name: str) -> User | None:
    async with Session() as session:
        stmt = select(User).where(User.full_name == full_name)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


async def is_user_registered(tg_id: int) -> bool:
    return await get_user_by_tg_id(tg_id) is not None


async def save_user_stats(volunteer_id: int, week: int, data_dict: dict):
    async with Session() as session:
        stmt = insert(UserStats).values(
            volunteer_id=volunteer_id,
            week=week,
            stats=data_dict
        )
        upsert_stmt = stmt.on_conflict_do_update(
            constraint='_volunteer_week_uc',
            set_=dict(
                stats=data_dict,
                last_modified=func.now()
            )
        )
        await session.execute(upsert_stmt)
        await session.commit()


async def merge_user_stats(volunteer_id: int, week: int, data_dict: dict):
    """Upsert, keeping stored answers for any key not supplied.

    The sheet importer uses this instead of save_user_stats: the sheet is
    authoritative, but a blank cell means "nothing recorded here", not "erase
    what the volunteer submitted". Rendered as
    `SET stats = statistics.stats || excluded.stats`.
    """
    async with Session() as session:
        stmt = insert(UserStats).values(
            volunteer_id=volunteer_id,
            week=week,
            stats=data_dict
        )
        upsert_stmt = stmt.on_conflict_do_update(
            constraint='_volunteer_week_uc',
            set_={
                "stats": UserStats.__table__.c.stats.op("||")(stmt.excluded.stats),
                "last_modified": func.now(),
            },
        )
        await session.execute(upsert_stmt)
        await session.commit()


async def sync_volunteers(names_list: list[str]):
    """Insert new roster names and delete volunteers absent from the roster.

    The sheet is authoritative for who exists, so this deletes -- which makes
    an empty roster catastrophic: `not_in([])` matches every row. Refuse that
    case rather than wiping the table and every statistics row with it.

    Previously the delete and the commit sat *inside* the per-name loop, so a
    30-name roster issued 30 deletes and 30 commits instead of one each.
    """
    if not names_list:
        raise ValueError(
            "Refusing to sync an empty volunteer roster: this would delete every volunteer."
        )

    # Preserve order but drop duplicates, so a name repeated in the sheet
    # cannot turn into two conflicting inserts.
    names = list(dict.fromkeys(names_list))

    async with Session() as session:
        stmt = insert(User).values([{"full_name": name} for name in names])
        stmt = stmt.on_conflict_do_nothing(index_elements=['full_name'])
        await session.execute(stmt)

        removed = await session.execute(
            delete(User).where(User.full_name.not_in(names)).returning(User.full_name)
        )
        removed_names = list(removed.scalars().all())
        if removed_names:
            print(f"sync_volunteers: removed {len(removed_names)} volunteer(s) absent from the sheet: {removed_names}")

        await session.commit()


async def register_user(user_db_id: int, tg_id: int) -> bool:
    """Bind a telegram account to a volunteer row.

    A single conditional UPDATE, so two people tapping the same name cannot
    both succeed. Previously each read `tg_id is None` in its own transaction
    and the later write silently replaced the earlier registration.
    """
    async with Session() as session:
        try:
            result = await session.execute(
                update(User)
                .where(User.id == user_db_id, User.tg_id.is_(None))
                .values(tg_id=tg_id)
            )
            await session.commit()
        except IntegrityError:
            # That telegram account is already bound to another volunteer.
            await session.rollback()
            return False
        return result.rowcount == 1


async def unregister_user(tg_id: int) -> bool:
    """Unbind a telegram account, keeping the volunteer and their statistics."""
    async with Session() as session:
        result = await session.execute(
            update(User).where(User.tg_id == tg_id).values(tg_id=None)
        )
        await session.commit()
        return result.rowcount == 1


async def export_data() -> list[WeekStats]:
    async with Session() as session:
        stmt = select(
            User.full_name, UserStats.stats, UserStats.week, UserStats.last_modified
        ).join(User, User.id == UserStats.volunteer_id)
        result = await session.execute(stmt)
        return [WeekStats(*row) for row in result.all()]


async def get_user_statistics(volunteer_id: int, week: int) -> list[WeekStats]:
    async with Session() as session:
        stmt = select(
            User.full_name, UserStats.stats, UserStats.week, UserStats.last_modified
        ).join(User, User.id == UserStats.volunteer_id).where(
            UserStats.volunteer_id == volunteer_id,
            UserStats.week == week,
        )
        result = await session.execute(stmt)
        return [WeekStats(*row) for row in result.all()]


async def get_all_statistics_for_user(
    volunteer_id: int, start_week: int = 0, end_week: int = 53
) -> list[WeekStats]:
    async with Session() as session:
        stmt = select(
            User.full_name, UserStats.stats, UserStats.week, UserStats.last_modified
        ).join(User, User.id == UserStats.volunteer_id).where(
            UserStats.volunteer_id == volunteer_id,
            UserStats.week >= start_week,
            UserStats.week <= end_week,
        ).order_by(asc(UserStats.week))
        result = await session.execute(stmt)
        return [WeekStats(*row) for row in result.all()]


async def get_all_registered_ids() -> list[int]:
    async with Session() as session:
        stmt = select(User.tg_id).where(User.tg_id.is_not(None))
        result = await session.execute(stmt)
        return list(result.scalars().all())
