"""Appointment tools — availability, book, reschedule, cancel (PRD 2.3).

Used by the Appointment agent after routing picks a department.
All mutating tools call assert_tool_scope before writing.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from core.graph_state import GraphState
from db.models import AppointmentStatus, SlotStatus
from db.repositories import (
    AppointmentRepository,
    DoctorRepository,
    SlotRepository,
)
from tools._scope import ToolScopeError, assert_tool_scope


def _require_patient_id(state: GraphState) -> str:
    patient_id = state.get("patient_id")
    if not patient_id:
        raise ToolScopeError("GraphState missing patient_id")
    return patient_id


def _slot_to_dict(slot, doctor_name: str | None = None) -> dict[str, Any]:
    return {
        "slot_id": slot.id,
        "doctor_id": slot.doctor_id,
        "doctor_name": doctor_name,
        "start_time": slot.start_time.isoformat() if slot.start_time else None,
        "end_time": slot.end_time.isoformat() if slot.end_time else None,
        "status": slot.status,
    }


def _appointment_to_dict(appt, *, doctor_name: str | None = None, slot=None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "appointment_id": appt.id,
        "patient_id": appt.patient_id,
        "doctor_id": appt.doctor_id,
        "doctor_name": doctor_name,
        "slot_id": appt.slot_id,
        "status": appt.status,
        "reason": appt.reason,
        "created_at": appt.created_at.isoformat() if appt.created_at else None,
    }
    if slot is not None:
        out["start_time"] = slot.start_time.isoformat() if slot.start_time else None
        out["end_time"] = slot.end_time.isoformat() if slot.end_time else None
    return out


def get_available_slots(
    db: Session,
    *,
    department_id: str | None = None,
    doctor_id: str | None = None,
    after: datetime | None = None,
    before: datetime | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """
    Query AVAILABLE slots from SQLite (real availability — not a fixed success string).

    Typically called with department_id from classify_intent / routing_result.
    Read-only — no patient scope check.
    """
    slots_repo = SlotRepository(db)
    doctors_repo = DoctorRepository(db)
    slots = slots_repo.list_available(
        doctor_id=doctor_id,
        department_id=department_id,
        after=after,
        before=before,
        limit=limit,
    )
    results = []
    for slot in slots:
        doctor = doctors_repo.get_by_id(slot.doctor_id)
        results.append(_slot_to_dict(slot, doctor.name if doctor else None))
    return {
        "count": len(results),
        "slots": results,
        "filters": {
            "department_id": department_id,
            "doctor_id": doctor_id,
        },
    }


def book_appointment(
    state: GraphState,
    db: Session,
    *,
    slot_id: str,
    reason: str | None = None,
) -> dict[str, Any]:
    """
    Book an AVAILABLE slot for state.patient_id.

    Order: scope check → load slot → book (repo marks slot BOOKED) → return DB row.
    If slot already taken → structured error (caller may retry next slot).
    """
    patient_id = _require_patient_id(state)
    assert_tool_scope(state, patient_id, db)

    slots_repo = SlotRepository(db)
    appts_repo = AppointmentRepository(db)
    doctors_repo = DoctorRepository(db)

    slot = slots_repo.get_by_id(slot_id)
    if slot is None:
        return {
            "ok": False,
            "error": "slot_not_found",
            "message": f"Slot {slot_id} does not exist",
        }
    if slot.status != SlotStatus.AVAILABLE.value:
        return {
            "ok": False,
            "error": "slot_unavailable",
            "message": f"Slot {slot_id} is {slot.status}, not AVAILABLE",
            "slot_status": slot.status,
        }

    try:
        appt = appts_repo.book(
            patient_id=patient_id,
            doctor_id=slot.doctor_id,
            slot=slot,
            reason=reason,
        )
    except ValueError as exc:
        return {"ok": False, "error": "booking_conflict", "message": str(exc)}

    doctor = doctors_repo.get_by_id(appt.doctor_id)
    return {
        "ok": True,
        "appointment": _appointment_to_dict(
            appt,
            doctor_name=doctor.name if doctor else None,
            slot=slot,
        ),
    }


def cancel_appointment(
    state: GraphState,
    db: Session,
    *,
    appointment_id: str,
) -> dict[str, Any]:
    """Cancel appointment and free the slot. Scope + ownership of patient_id."""
    patient_id = _require_patient_id(state)
    assert_tool_scope(state, patient_id, db)

    appts_repo = AppointmentRepository(db)
    doctors_repo = DoctorRepository(db)
    slots_repo = SlotRepository(db)

    appt = appts_repo.get_by_id(appointment_id)
    if appt is None:
        return {"ok": False, "error": "not_found", "message": "Appointment not found"}

    if appt.patient_id != patient_id:
        raise ToolScopeError("Cannot cancel another patient's appointment")

    if appt.status == AppointmentStatus.CANCELLED.value:
        return {
            "ok": False,
            "error": "already_cancelled",
            "message": "Appointment is already cancelled",
        }

    appts_repo.cancel(appt)
    doctor = doctors_repo.get_by_id(appt.doctor_id)
    slot = slots_repo.get_by_id(appt.slot_id)
    return {
        "ok": True,
        "appointment": _appointment_to_dict(
            appt,
            doctor_name=doctor.name if doctor else None,
            slot=slot,
        ),
    }


def reschedule_appointment(
    state: GraphState,
    db: Session,
    *,
    appointment_id: str,
    new_slot_id: str,
) -> dict[str, Any]:
    """Move appointment to a new AVAILABLE slot; free the old one."""
    patient_id = _require_patient_id(state)
    assert_tool_scope(state, patient_id, db)

    appts_repo = AppointmentRepository(db)
    slots_repo = SlotRepository(db)
    doctors_repo = DoctorRepository(db)

    appt = appts_repo.get_by_id(appointment_id)
    if appt is None:
        return {"ok": False, "error": "not_found", "message": "Appointment not found"}
    if appt.patient_id != patient_id:
        raise ToolScopeError("Cannot reschedule another patient's appointment")
    if appt.status == AppointmentStatus.CANCELLED.value:
        return {
            "ok": False,
            "error": "cancelled",
            "message": "Cannot reschedule a cancelled appointment",
        }

    new_slot = slots_repo.get_by_id(new_slot_id)
    if new_slot is None:
        return {"ok": False, "error": "slot_not_found", "message": "New slot not found"}
    if new_slot.status != SlotStatus.AVAILABLE.value:
        return {
            "ok": False,
            "error": "slot_unavailable",
            "message": f"New slot is {new_slot.status}",
            "slot_status": new_slot.status,
        }

    try:
        appts_repo.reschedule(appt, new_slot=new_slot)
    except ValueError as exc:
        return {"ok": False, "error": "reschedule_conflict", "message": str(exc)}

    doctor = doctors_repo.get_by_id(appt.doctor_id)
    return {
        "ok": True,
        "appointment": _appointment_to_dict(
            appt,
            doctor_name=doctor.name if doctor else None,
            slot=new_slot,
        ),
    }
