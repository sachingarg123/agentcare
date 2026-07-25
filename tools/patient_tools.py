"""Patient tools — ensure PatientProfile exists for the workflow subject (PRD 2.1).

Used by Coordinator at workflow start (coordinator_init).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from core.graph_state import GraphState
from db.models import UserRole
from db.repositories import PatientRepository, UserRepository
from tools._scope import ToolScopeError, assert_tool_scope


def get_or_create_patient(
    state: GraphState,
    db: Session,
    *,
    phone: str | None = None,
    preferred_language: str | None = None,
    emergency_contact: str | None = None,
    date_of_birth: date | None = None,
) -> dict[str, Any]:
    """
    Ensure a PatientProfile exists and return structured data from the DB.

    - PATIENT actor: upsert profile for actor_user_id; patient_id becomes that profile.
    - STAFF/ADMIN: state must already include patient_id; load that profile.

    Order: validate scope inputs first, then mutate, then assert_tool_scope.
    Does not commit — caller commits the session.
    """
    actor_user_id = state.get("actor_user_id")
    actor_role = state.get("actor_role")
    if not actor_user_id or not actor_role:
        raise ToolScopeError("GraphState missing actor_user_id or actor_role")

    patients = PatientRepository(db)
    users = UserRepository(db)
    created = False

    if actor_role == UserRole.PATIENT.value:
        existing = patients.get_by_user_id(actor_user_id)

        # --- Validate BEFORE any flush/create ---
        # If GraphState already has patient_id, it must be this actor's profile.
        stated_patient_id = state.get("patient_id")
        if stated_patient_id:
            stated_profile = patients.get_by_id(stated_patient_id)
            if stated_profile is None or stated_profile.user_id != actor_user_id:
                raise ToolScopeError(
                    "GraphState patient_id does not match actor's PatientProfile"
                )

        # --- Mutate only after checks pass ---
        if existing is None:
            user = users.get_by_id(actor_user_id)
            if user is None or user.role != UserRole.PATIENT.value:
                raise ToolScopeError("Cannot create patient profile for non-patient user")
            kwargs: dict[str, Any] = {}
            if phone is not None:
                kwargs["phone"] = phone
            if preferred_language is not None:
                kwargs["preferred_language"] = preferred_language
            if emergency_contact is not None:
                kwargs["emergency_contact"] = emergency_contact
            if date_of_birth is not None:
                kwargs["date_of_birth"] = date_of_birth
            profile = patients.create(user_id=actor_user_id, **kwargs)
            created = True
        else:
            profile = existing
            if phone is not None:
                profile.phone = phone
            if preferred_language is not None:
                profile.preferred_language = preferred_language
            if emergency_contact is not None:
                profile.emergency_contact = emergency_contact
            if date_of_birth is not None:
                profile.date_of_birth = date_of_birth
            db.flush()

    else:
        patient_id = state.get("patient_id")
        if not patient_id:
            raise ToolScopeError(
                "STAFF/ADMIN workflows must set patient_id before get_or_create_patient"
            )
        profile = patients.get_by_id(patient_id)
        if profile is None:
            raise ToolScopeError(f"PatientProfile not found: {patient_id}")

    # Final shared gate (profile now exists, so PATIENT actor check can run)
    scoped_state: GraphState = {
        **state,
        "patient_id": profile.id,
        "actor_user_id": actor_user_id,
        "actor_role": actor_role,
    }
    assert_tool_scope(scoped_state, profile.id, db)

    user = users.get_by_id(profile.user_id)
    return {
        "patient_id": profile.id,
        "user_id": profile.user_id,
        "name": user.name if user else None,
        "email": user.email if user else None,
        "phone": profile.phone,
        "preferred_language": profile.preferred_language,
        "emergency_contact": profile.emergency_contact,
        "date_of_birth": profile.date_of_birth.isoformat()
        if profile.date_of_birth
        else None,
        "created": created,
    }
