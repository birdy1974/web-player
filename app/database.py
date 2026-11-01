"""web-player — database (SQLite via SQLAlchemy async).

One file in the data volume. WAL mode so a reader never blocks the writer,
foreign keys enforced, and the schema created/updated at start-up.
"""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import DB_PATH

log = logging.getLogger("web-player.db")


class Base(DeclarativeBase):
    pass


engine = create_async_engine(
    f"sqlite+aiosqlite:///{DB_PATH}",
    future=True,
    echo=False,
    connect_args={"timeout": 30},
)


@event.listens_for(engine.sync_engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=30000")
    cur.close()


SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db():
    """FastAPI dependency — one session per request."""
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    """Create the schema and the first administrator."""
    from . import models  # noqa: F401  (registers the tables)
    from .security import hash_password

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    log.info("schema ready at %s", DB_PATH)

    from .models import User
    from sqlalchemy import select

    async with SessionLocal() as session:
        rows = (await session.execute(select(User))).scalars().all()
        if not rows:
            from .config import ADMIN_PASSWORD, ADMIN_USER

            password = ADMIN_PASSWORD or "web-player"
            session.add(User(
                name=ADMIN_USER,
                password_hash=hash_password(password),
                role="admin",
                enabled=True,
                max_streams=10,
            ))
            await session.commit()
            if not ADMIN_PASSWORD:
                log.warning(
                    "WEB_PLAYER_ADMIN_PASSWORD is not set — the console password is %r. "
                    "Set it in .env and restart.", password)
            else:
                log.info("administrator %r created", ADMIN_USER)


async def db_healthy() -> bool:
    try:
        async with SessionLocal() as session:
            await session.execute(text("select 1"))
        return True
    except Exception:  # noqa: BLE001
        return False


def database_file() -> Path:
    return DB_PATH
