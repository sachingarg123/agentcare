"""Phase 3.4 — routing_node: intent + department, HITL on low confidence."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from agents.routing_node import ROUTING_PROMPT, get_routing_tools, routing_node
from auth.passwords import hash_password
from core.graph_state import GraphState
from db.models import Base, EscalationStatus, UserRole
from db.repositories import (
    DepartmentRepository,
    EscalationRepository,
    PatientRepository,
    UserRepository,
    WorkflowRepository,
)


def _session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _seed(db: Session, raw_request: str, *, safe: bool = True) -> GraphState:
    user = UserRepository(db).create(
        name="Asha",
        email="asha-routing-node@ex.com",
        password_hash=hash_password("x"),
        role=UserRole.PATIENT.value,
    )
    profile = PatientRepository(db).create(user_id=user.id)
    wf = WorkflowRepository(db).create(patient_id=profile.id)
    for name in ("Cardiology", "Radiology", "General Medicine", "Orthopedics", "Dermatology"):
        DepartmentRepository(db).create(name=name, description=name)
    db.commit()
    state: GraphState = {
        "workflow_run_id": wf.id,
        "patient_id": profile.id,
        "actor_user_id": user.id,
        "actor_role": UserRole.PATIENT.value,
        "raw_request": raw_request,
        "safety_result": {
            "safe": safe,
            "flags": [] if safe else ["clinical"],
            "reason": "ok" if safe else "blocked",
        },
    }
    return state


def test_routing_prompt_loaded():
    assert "Routing" in ROUTING_PROMPT
    assert "classify_intent" in ROUTING_PROMPT


def test_routing_node_maps_cardiology():
    db = _session()
    state = _seed(
        db,
        "I need a cardiology follow-up next week and want to attach my old ECG.",
    )
    update = routing_node(state, db)
    db.commit()

    assert update["current_step"] == "routing"
    assert update["hitl_required"] is False
    rr = update["routing_result"]
    assert rr["department_name"] == "Cardiology"
    assert rr["department_id"]
    assert rr["needs_staff_review"] is False
    assert rr["confidence"] >= 0.5
    assert "BOOK_APPOINTMENT" in update["administrative_intents"] or (
        "FOLLOWUP_VISIT" in update["administrative_intents"]
    )
    assert "UPLOAD_DOCUMENT" in update["administrative_intents"]


def test_routing_node_low_confidence_sets_hitl_and_escalation():
    db = _session()
    state = _seed(db, "hello")
    update = routing_node(state, db)
    db.commit()

    assert update["hitl_required"] is True
    assert update["routing_result"]["needs_staff_review"] is True
    assert "escalation_id=" in (update.get("hitl_reason") or "")
    pending = EscalationRepository(db).list_pending()
    assert any(e.workflow_run_id == state["workflow_run_id"] for e in pending)
    assert pending[0].status == EscalationStatus.PENDING.value


def test_routing_node_refuses_after_failed_safety():
    db = _session()
    state = _seed(db, "Book cardiology", safe=False)
    update = routing_node(state, db)

    assert update["current_step"] == "routing"
    assert update.get("error")
    assert update["hitl_required"] is True
    assert "routing_result" not in update


def test_get_routing_tools_binds_three_tools():
    db = _session()
    state = _seed(db, "Book heart appointment")
    tools = get_routing_tools(state, db)
    names = {t.name for t in tools}
    assert names == {"lookup_departments", "classify_intent", "create_escalation"}
    depts = tools[0].invoke({})
    assert any(d["name"] == "Cardiology" for d in depts)
