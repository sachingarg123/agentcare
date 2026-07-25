"""Phase 2.2 — routing tools against real department rows."""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core.graph_state import GraphState
from db.models import Base
from db.repositories import DepartmentRepository
from tools.routing_tools import (
    INTENT_BOOK,
    INTENT_FOLLOWUP,
    INTENT_UPLOAD,
    classify_intent,
    lookup_departments,
)


def _db_with_depts() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    repo = DepartmentRepository(db)
    for name, desc in [
        ("Cardiology", "Heart"),
        ("Radiology", "Imaging"),
        ("General Medicine", "GP"),
        ("Orthopedics", "Bones"),
        ("Dermatology", "Skin"),
    ]:
        repo.create(name=name, description=desc)
    db.commit()
    return db


def test_lookup_departments_reads_db():
    db = _db_with_depts()
    rows = lookup_departments(db)
    assert len(rows) == 5
    names = {r["name"] for r in rows}
    assert "Cardiology" in names
    assert all(r["department_id"] and r["active"] for r in rows)


def test_classify_cardiology_followup_with_ecg():
    db = _db_with_depts()
    state: GraphState = {
        "raw_request": "I need a cardiology follow-up next week and want to attach my old ECG.",
        "actor_user_id": "u1",
        "actor_role": "PATIENT",
    }
    result = classify_intent(state, db)
    assert result["department_name"] == "Cardiology"
    assert result["department_id"]
    assert INTENT_FOLLOWUP in result["intents"] or INTENT_BOOK in result["intents"]
    assert INTENT_UPLOAD in result["intents"]
    assert result["confidence"] >= 0.5
    assert result["needs_staff_review"] is False


def test_classify_low_confidence_triggers_staff_flag():
    db = _db_with_depts()
    state: GraphState = {"raw_request": "hello"}
    result = classify_intent(state, db)
    assert result["needs_staff_review"] is True
    assert result["confidence"] < 0.5


def test_classify_physician_checkup_to_general_medicine():
    db = _db_with_depts()
    result = classify_intent(
        {
            "raw_request": (
                "I need a medical check up and schedule an appointment with a physician"
            )
        },
        db,
    )
    assert result["department_name"] == "General Medicine"
    assert INTENT_BOOK in result["intents"]
    assert result["confidence"] >= 0.5
    assert result["needs_staff_review"] is False


def test_classify_uses_db_departments_not_hardcoded_ids():
    db = _db_with_depts()
    cardiology = next(d for d in lookup_departments(db) if d["name"] == "Cardiology")
    result = classify_intent(
        {"raw_request": "Book heart appointment"},
        db,
    )
    assert result["department_id"] == cardiology["department_id"]
