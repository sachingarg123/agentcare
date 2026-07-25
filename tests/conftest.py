"""Shared pytest fixtures — isolated in-memory DB with seed users for auth tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from auth.passwords import hash_password
from core.config import get_settings
from db.models import Base, UserRole
from db.repositories import PatientRepository, UserRepository, WorkflowRepository
from db.session import get_db
from main import app

DEMO_PASSWORD = "password123"


@pytest.fixture(autouse=True)
def _offline_llm(monkeypatch):
    """Keep pytest deterministic — pipeline LLM stages off unless a test opts in."""
    monkeypatch.setenv("USE_LLM", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def db_session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = SessionLocal()

    users = UserRepository(session)
    patients = PatientRepository(session)
    pw = hash_password(DEMO_PASSWORD)

    asha = users.create(
        name="Asha Patient",
        email="asha.patient@example.com",
        password_hash=pw,
        role=UserRole.PATIENT.value,
    )
    ravi = users.create(
        name="Ravi Patient",
        email="ravi.patient@example.com",
        password_hash=pw,
        role=UserRole.PATIENT.value,
    )
    users.create(
        name="Sam Staff",
        email="sam.staff@example.com",
        password_hash=pw,
        role=UserRole.STAFF.value,
    )
    users.create(
        name="Ada Admin",
        email="ada.admin@example.com",
        password_hash=pw,
        role=UserRole.ADMIN.value,
    )
    asha_profile = patients.create(user_id=asha.id, phone="+91-1")
    ravi_profile = patients.create(user_id=ravi.id, phone="+91-2")

    # One workflow owned by Asha (patient A)
    wf = WorkflowRepository(session).create(
        patient_id=asha_profile.id,
        current_step="coordinator_init",
        state={"demo": True},
    )
    session.commit()

    # Stash ids for tests
    session.info["asha_user_id"] = asha.id
    session.info["ravi_user_id"] = ravi.id
    session.info["asha_patient_id"] = asha_profile.id
    session.info["ravi_patient_id"] = ravi_profile.id
    session.info["asha_workflow_id"] = wf.id

    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def client(db_session: Session) -> TestClient:
    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def login(client: TestClient, email: str, password: str = DEMO_PASSWORD) -> str:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
