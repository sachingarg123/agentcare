"""Phase 2.6 — safety screen, escalation, audit tools."""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from auth.passwords import hash_password
from core.graph_state import GraphState
from db.models import Base, EscalationStatus, UserRole
from db.repositories import (
    AuditRepository,
    EscalationRepository,
    PatientRepository,
    UserRepository,
    WorkflowRepository,
)
from tools.safety_tools import (
    block_unsafe_action,
    create_escalation,
    screen_request,
    write_audit_event,
)


def _session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _seed_workflow(db: Session) -> GraphState:
    user = UserRepository(db).create(
        name="Asha",
        email="asha-safe@ex.com",
        password_hash=hash_password("x"),
        role=UserRole.PATIENT.value,
    )
    profile = PatientRepository(db).create(user_id=user.id)
    wf = WorkflowRepository(db).create(patient_id=profile.id)
    db.commit()
    return {
        "workflow_run_id": wf.id,
        "patient_id": profile.id,
        "actor_user_id": user.id,
        "actor_role": UserRole.PATIENT.value,
        "raw_request": "",
    }


def test_screen_blocks_prescription_request():
    state: GraphState = {"raw_request": "What medicine should I take for chest pain?"}
    result = screen_request(state)
    assert result["safe"] is False
    assert "prescription" in result["flags"] or result["category"] == "PRESCRIPTION"


def test_screen_blocks_diagnosis_request():
    result = screen_request({"raw_request": "Do I have diabetes?"})
    assert result["safe"] is False
    assert result["category"] == "DIAGNOSIS"


def test_screen_allows_admin_appointment_request():
    result = screen_request(
        {"raw_request": "I need a cardiology follow-up next week and want to attach my ECG."}
    )
    assert result["safe"] is True
    assert result["flags"] == []


def test_create_escalation_and_audit():
    db = _session()
    state = _seed_workflow(db)
    state["raw_request"] = "What medicine should I take?"

    screen = screen_request(state)
    esc = create_escalation(state, db, reason=screen["reason"])
    audit = write_audit_event(
        state,
        db,
        action="safety.block",
        entity_type="Escalation",
        entity_id=esc["escalation_id"],
    )
    db.commit()

    assert esc["ok"] is True
    assert esc["status"] == EscalationStatus.PENDING.value
    assert EscalationRepository(db).get_by_id(esc["escalation_id"]) is not None
    assert audit["ok"] is True
    events = AuditRepository(db).list_for_entity("Escalation", esc["escalation_id"])
    assert len(events) == 1
    assert events[0].event_metadata.get("role") == UserRole.PATIENT.value


def test_block_unsafe_action_combines_escalation_and_audit():
    db = _session()
    state = _seed_workflow(db)
    state["raw_request"] = "Should I get surgery for this?"
    out = block_unsafe_action(state, db)
    db.commit()
    assert out["blocked"] is True
    assert out["escalation"]["ok"] is True
    assert out["audit"]["ok"] is True
