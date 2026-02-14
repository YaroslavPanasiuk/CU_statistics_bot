from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import BigInteger, ForeignKey, func, DateTime, UniqueConstraint, select, delete, asc
from sqlalchemy.dialects.postgresql import insert, JSONB
from datetime import datetime
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
        UniqueConstraint('tg_id', 'week', name='_user_week_uc'),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("volunteers.tg_id", ondelete="CASCADE"))
    week: Mapped[int] = mapped_column()
    stats: Mapped[dict] = mapped_column(JSONB, default={})
    last_modified: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        server_default=func.now(), 
        onupdate=func.now()
    )

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

async def get_unregistered_users():
    async with Session() as session:
        stmt = select(User).where(User.tg_id == None).order_by(asc(User.id))
        result = await session.execute(stmt)
        return result.scalars().all()

async def get_user_by_tg_id(tg_id: int) -> User | None:
    async with Session() as session:
        stmt = select(User).where(User.tg_id == tg_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()
    
async def is_user_registered(tg_id: int) -> bool:
    async with Session() as session:
        stmt = select(User).where(User.tg_id == tg_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        return user is not None

async def save_user_stats(tg_id: int, week: int, data_dict: dict):
    async with Session() as session:
        stmt = insert(UserStats).values(
            tg_id=tg_id,
            week=week,
            stats=data_dict
        )
        
        upsert_stmt = stmt.on_conflict_do_update(
            constraint='_user_week_uc',
            set_=dict(
                stats=data_dict,
                last_modified=func.now()
            )
        )
        
        await session.execute(upsert_stmt)
        await session.commit()

async def sync_volunteers(names_list: list[str]):
    async with Session() as session:
        for name in names_list:
            stmt = insert(User).values(full_name=name)
            stmt = stmt.on_conflict_do_nothing(index_elements=['full_name'])
            
            await session.execute(stmt)

            stmt = delete(User).where(
                User.full_name.not_in(names_list)
            )
            await session.execute(stmt)
            await session.commit()
        
        await session.commit()

async def register_user(user_db_id: int, tg_id: int) -> bool:
    async with Session() as session:
        user = await session.get(User, user_db_id)
        
        if user and user.tg_id is None:
            user.tg_id = tg_id
            await session.commit()
            return True
        return False
    
async def export_data():
    async with Session() as session:
        stmt = select(User.full_name, UserStats.week, UserStats.stats, UserStats.last_modified).join(
            User, User.tg_id == UserStats.tg_id
        )
        result = await session.execute(stmt)
        return result.all()
    
async def get_user_statistics(tg_id, week):
    async with Session() as session:
        stmt = select(User.full_name, UserStats.stats, UserStats.last_modified).join(
            User, User.tg_id == UserStats.tg_id
        ).where(User.tg_id == tg_id and UserStats.week == week)
        result = await session.execute(stmt)
        return result.all()
    
async def get_all_registered_ids() -> list[int]:
    async with Session() as session:
        stmt = select(User.tg_id).where(User.tg_id.is_not(None))
        result = await session.execute(stmt)
        
        return list(result.scalars().all())