"""Phase 5.1 — lifespan bootstrap: schema + seed-if-empty."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from core.bootstrap import bootstrap_database
from core.config import get_settings
from db.models import Base, User
from db.repositories import UserRepository
from scripts.seed_data import SEED_USERS, is_seeded, seed_if_empty


@pytest.fixture()
def temp_db(tmp_path: Path, monkeypatch):
    """Point app settings at a fresh SQLite file under tmp_path."""
    db_path = tmp_path / "bootstrap.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("CHECKPOINT_DB_PATH", str(tmp_path / "checkpoints.db"))
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    get_settings.cache_clear()

    # Rebind engine used by db.session / bootstrap after settings change
    import db.session as db_session
    from core.config import get_settings as _gs

    settings = _gs()
    settings.ensure_data_dirs()
    new_engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
    )
    db_session.engine = new_engine
    db_session.SessionLocal = sessionmaker(
        bind=new_engine, autocommit=False, autoflush=False
    )

    yield db_path, new_engine

    new_engine.dispose()
    get_settings.cache_clear()


def test_bootstrap_creates_schema_and_seeds(temp_db):
    db_path, engine = temp_db
    assert not db_path.exists() or db_path.stat().st_size == 0

    result = bootstrap_database()
    assert result["schema_ready"] is True
    assert result["seeded"] is True
    assert db_path.exists()

    names = set(inspect(engine).get_table_names())
    assert "users" in names
    assert "departments" in names
    assert "workflow_runs" in names

    from db.session import SessionLocal

    db = SessionLocal()
    try:
        assert is_seeded(db)
        asha = UserRepository(db).get_by_email(SEED_USERS[0]["email"])
        assert asha is not None
        assert asha.role == "PATIENT"
    finally:
        db.close()


def test_bootstrap_second_call_does_not_reseed(temp_db):
    first = bootstrap_database()
    assert first["seeded"] is True

    from db.session import SessionLocal

    db = SessionLocal()
    try:
        count_before = len(db.query(User).all())
    finally:
        db.close()

    second = bootstrap_database()
    assert second["seeded"] is False
    assert second["schema_ready"] is True

    db = SessionLocal()
    try:
        count_after = len(db.query(User).all())
        assert count_after == count_before
        assert seed_if_empty() is False
    finally:
        db.close()
