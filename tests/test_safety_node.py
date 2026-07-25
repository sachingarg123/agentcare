"""Phase 3.3 — safety_node: rules first, escalate clinical prompts."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from agents.safety_node import SAFETY_PROMPT, get_safety_tools, safety_node
from auth.passwords import hash_password
from core.graph_state import GraphState
from db.models import Base, EscalationStatus, UserRole
from db.repositories import EscalationRepository, PatientRepository, UserRepository, WorkflowRepository


def _session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _seed_state(db: Session, raw_request: str) -> GraphState:
    user = UserRepository(db).create(
        name="Asha",
        email="asha-safety-node@ex.com",
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
        "raw_request": raw_request,
    }


def test_safety_prompt_loaded():
    assert "Safety" in SAFETY_PROMPT
    assert "screen_request" in SAFETY_PROMPT


def test_safety_node_allows_admin_request():
    db = _session()
    state = _seed_state(
        db,
        "I need a cardiology follow-up next week and want to attach my ECG.",
    )
    update = safety_node(state, db)
    db.commit()

    assert update["current_step"] == "safety"
    assert update["hitl_required"] is False
    assert update["safety_result"]["safe"] is True
    assert update["safety_result"]["blocked"] is False
    pending = EscalationRepository(db).list_pending()
    assert all(e.workflow_run_id != state["workflow_run_id"] for e in pending)


def test_safety_node_blocks_clinical_prompt_and_escalates():
    db = _session()
    state = _seed_state(db, "What medicine should I take for chest pain?")
    update = safety_node(state, db)
    db.commit()

    assert update["current_step"] == "safety"
    assert update["hitl_required"] is True
    assert update["hitl_reason"]
    result = update["safety_result"]
    assert result["safe"] is False
    assert result["blocked"] is True
    assert result["escalation_id"]
    assert result["safe_alternative"]

    esc = EscalationRepository(db).get_by_id(result["escalation_id"])
    assert esc is not None
    assert esc.status == EscalationStatus.PENDING.value
    assert esc.workflow_run_id == state["workflow_run_id"]


def test_get_safety_tools_binds_four_tools():
    db = _session()
    state = _seed_state(db, "Book appointment")
    tools = get_safety_tools(state, db)
    names = {t.name for t in tools}
    assert names == {
        "screen_request",
        "create_escalation",
        "block_unsafe_action",
        "write_audit_event",
    }
    # Bound tool can screen without re-passing full state
    out = tools[0].invoke({"raw_request": "Do I have diabetes?"})
    assert out["safe"] is False
