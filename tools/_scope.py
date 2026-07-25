"""Tool-level RBAC — agents must not write another patient's data (PRD §7.1).

Called at the start of mutating tools (book, store document, create reminder, …).
This is Layer 3 of access control (after route RBAC + HTTP ownership).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from core.graph_state import GraphState
from db.models import UserRole
from db.repositories import PatientRepository


class ToolScopeError(PermissionError):
    """Raised when a tool attempts cross-patient or unauthorized actor access."""


def assert_tool_scope(
    state: GraphState,
    target_patient_id: str,
    db: Session,
) -> None:
    """
    Enforce that this tool invocation stays on the workflow's patient.

    Rules:
    1. target_patient_id must equal state['patient_id'] (workflow subject).
    2. If actor is PATIENT, their PatientProfile.id must equal state['patient_id']
       (a patient cannot run/write another patient's workflow).
    3. STAFF / ADMIN may act on a patient's workflow (audited via actor_user_id),
       but still only for state['patient_id'] — not an arbitrary third patient.
    """
    workflow_patient_id = state.get("patient_id")
    if not workflow_patient_id:
        raise ToolScopeError("GraphState missing patient_id")

    if target_patient_id != workflow_patient_id:
        raise ToolScopeError(
            f"Tool cannot act on patient {target_patient_id}; "
            f"workflow subject is {workflow_patient_id}"
        )

    actor_role = state.get("actor_role")
    actor_user_id = state.get("actor_user_id")
    if not actor_role or not actor_user_id:
        raise ToolScopeError("GraphState missing actor_user_id or actor_role")

    if actor_role == UserRole.PATIENT.value:
        profile = PatientRepository(db).get_by_user_id(actor_user_id)
        if profile is None:
            raise ToolScopeError("Patient actor has no PatientProfile")
        if profile.id != workflow_patient_id:
            raise ToolScopeError(
                "Patient cannot run workflow for another patient"
            )
    elif actor_role not in (
        UserRole.STAFF.value,
        UserRole.ADMIN.value,
        UserRole.PATIENT.value,
    ):
        raise ToolScopeError(f"Unknown actor_role: {actor_role}")
