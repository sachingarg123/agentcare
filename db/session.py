"""Database engine + session factory (needed by Alembic and later repositories)."""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from core.config import get_settings

_settings = get_settings()
_settings.ensure_data_dirs()

# check_same_thread=False is required for SQLite used across FastAPI request threads.
engine = create_engine(
    _settings.database_url,
    connect_args={"check_same_thread": False},
    echo=_settings.debug,
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, class_=Session)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: yield a DB session and always close it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
