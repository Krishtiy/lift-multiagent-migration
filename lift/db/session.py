"""Database engine and session management."""

import os
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, Engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool

from lift.db.models import Base

DEFAULT_DB_URL = os.environ.get(
    "LIFT_DATABASE_URL",
    "sqlite:///lift.db"
)


def create_db_engine(db_url: str = DEFAULT_DB_URL, echo: bool = False) -> Engine:
    """Create SQLAlchemy engine with connection pool settings."""
    if db_url.startswith("sqlite"):
        connect_args = {"check_same_thread": False, "timeout": 30}
        if ":memory:" in db_url:
            engine = create_engine(
                db_url,
                echo=echo,
                connect_args=connect_args,
                poolclass=StaticPool,
            )
        else:
            engine = create_engine(db_url, echo=echo, connect_args=connect_args)

        from sqlalchemy import event

        @event.listens_for(engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode = WAL;")
            cursor.execute("PRAGMA synchronous = NORMAL;")
            cursor.execute("PRAGMA busy_timeout = 5000;")
            cursor.close()

        return engine

    return create_engine(db_url, echo=echo, pool_pre_ping=True, pool_size=10, max_overflow=20)



def init_db(engine: Engine) -> None:
    """Create all schema tables (useful for unit tests and local dev)."""
    Base.metadata.create_all(bind=engine)


def drop_db(engine: Engine) -> None:
    """Drop all tables."""
    Base.metadata.drop_all(bind=engine)


import threading

_db_lock = threading.Lock()


@contextmanager
def get_db(engine: Engine) -> Generator[Session, None, None]:
    """Context manager for acquiring and closing a database session."""
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = session_factory()
    is_sqlite = engine.dialect.name == "sqlite"
    try:
        yield session
        if is_sqlite:
            with _db_lock:
                session.commit()
        else:
            session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

