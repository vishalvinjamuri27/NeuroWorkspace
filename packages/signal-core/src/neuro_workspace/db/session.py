"""SQLAlchemy engine/session construction."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker


def create_engine_and_factory(
    database_url: str,
    *,
    pool_size: int = 10,
    max_overflow: int = 20,
    pool_timeout: int = 30,
) -> tuple[Engine, sessionmaker[Session]]:
    is_sqlite = database_url.startswith("sqlite")
    connect_args: dict[str, Any] = {"check_same_thread": False} if is_sqlite else {}
    # SQLite's default poolclass (SingletonThreadPool/NullPool depending on
    # URL) does not accept pool_size/max_overflow -- only Postgres gets the
    # explicit sizing here. See Settings.db_pool_size for why the defaults
    # above are not SQLAlchemy's own (5/10): that combination was measured
    # (scripts/bench_job_concurrency.py) to start timing out connection
    # checkout under realistic concurrency -- see docs/PERFORMANCE.md.
    pool_kwargs: dict[str, Any] = (
        {}
        if is_sqlite
        else {"pool_size": pool_size, "max_overflow": max_overflow, "pool_timeout": pool_timeout}
    )
    engine = create_engine(
        database_url, pool_pre_ping=True, connect_args=connect_args, **pool_kwargs
    )
    if database_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def set_sqlite_pragma(dbapi_connection: object, connection_record: object) -> None:
            del connection_record
            cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()
