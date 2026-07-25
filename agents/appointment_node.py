"""Appointment agent node — slot search + book with retry (Phase 3.5).

Deterministic node: uses routing_result.department_id, retries alternate slots
on conflict. Also exposes LangChain StructuredTools for optional LLM binding.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from sqlalchemy.orm import Session

from agents.prompts import load_prompt
from core.graph_state import AppointmentResult, GraphState
from tools.appointment_tools import (
    book_appointment,
    cancel_appointment,
    get_available_slots,
    reschedule_appointment,
)
from tools.routing_tools import INTENT_CANCEL, INTENT_RESCHEDULE
from tools.safety_tools import write_audit_event

APPOINTMENT_PROMPT = load_prompt("appointment")

_RETRYABLE = frozenset({"slot_unavailable", "booking_conflict"})


def _audit_appointment(
    state: GraphState,
    db: Session,
    *,
    action: str,
    result: dict[str, Any],
) -> None:
    if not state.get("actor_user_id"):
        return
    write_audit_event(
        state,
        db,
        action=action,
        entity_type="Appointment",
        entity_id=result.get("appointment_id"),
        event_metadata={
            "ok": result.get("ok"),
            "error": result.get("error"),
            "slot_id": result.get("slot_id"),
            "status": result.get("status"),
        },
    )


def _intents(state: GraphState) -> list[str]:
    return list(
        state.get("administrative_intents")
        or (state.get("routing_result") or {}).get("intents")
        or []
    )


def _department_id(state: GraphState) -> str | None:
    routing = state.get("routing_result") or {}
    return routing.get("department_id")


def _from_booked(booked: dict[str, Any]) -> AppointmentResult:
    appt = booked.get("appointment") or {}
    return {
        "ok": True,
        "appointment_id": appt.get("appointment_id"),
        "slot_id": appt.get("slot_id"),
        "status": appt.get("status"),
        "doctor_id": appt.get("doctor_id"),
        "doctor_name": appt.get("doctor_name"),
        "start_time": appt.get("start_time"),
        "end_time": appt.get("end_time"),
        "reason": appt.get("reason"),
    }


def appointment_node(
    state: GraphState,
    db: Session,
    *,
    max_retries: int = 5,
    reason: str | None = None,
) -> GraphState:
    """
    Book (with slot retry), cancel, or reschedule based on intents.

    Default path: list available slots for routed department → book first;
    on conflict/unavailable, try the next slot up to ``max_retries``.

    Does not commit — caller commits the session.
    """
    if not state.get("patient_id"):
        result = {
            "ok": False,
            "error": "missing_patient_id",
            "message": "GraphState missing patient_id",
        }
        _audit_appointment(state, db, action="appointment.fail", result=result)
        return {
            "current_step": "appointment",
            "appointment_result": result,
            "error": "GraphState missing patient_id",
        }

    intents = _intents(state)
    existing_id = (state.get("appointment_result") or {}).get("appointment_id")

    # --- Cancel ---
    if INTENT_CANCEL in intents and existing_id:
        cancelled = cancel_appointment(state, db, appointment_id=existing_id)
        if cancelled.get("ok"):
            result = _from_booked(cancelled)
            result["status"] = result.get("status") or "CANCELLED"
            _audit_appointment(state, db, action="appointment.cancel", result=result)
            return {
                "current_step": "appointment",
                "appointment_result": result,
                "hitl_required": False,
                "hitl_reason": None,
            }
        result = {
            "ok": False,
            "error": cancelled.get("error"),
            "message": cancelled.get("message"),
            "appointment_id": existing_id,
        }
        _audit_appointment(state, db, action="appointment.fail", result=result)
        return {
            "current_step": "appointment",
            "appointment_result": result,
        }

    # --- Reschedule ---
    if INTENT_RESCHEDULE in intents and existing_id:
        dept_id = _department_id(state)
        available = get_available_slots(db, department_id=dept_id, limit=max_retries)
        slots = available.get("slots") or []
        if not slots:
            result = {
                "ok": False,
                "error": "no_slots",
                "message": "No available slots to reschedule into",
                "appointment_id": existing_id,
                "available_count": 0,
            }
            _audit_appointment(state, db, action="appointment.fail", result=result)
            return {
                "current_step": "appointment",
                "appointment_result": result,
                "hitl_required": True,
                "hitl_reason": "No available slots for reschedule",
            }
        last_err: dict[str, Any] = {}
        for slot in slots[:max_retries]:
            moved = reschedule_appointment(
                state,
                db,
                appointment_id=existing_id,
                new_slot_id=slot["slot_id"],
            )
            if moved.get("ok"):
                result = _from_booked(moved)
                _audit_appointment(
                    state, db, action="appointment.reschedule", result=result
                )
                return {
                    "current_step": "appointment",
                    "appointment_result": result,
                    "hitl_required": False,
                    "hitl_reason": None,
                }
            last_err = moved
            if moved.get("error") not in _RETRYABLE:
                break
        result = {
            "ok": False,
            "error": last_err.get("error") or "reschedule_failed",
            "message": last_err.get("message"),
            "appointment_id": existing_id,
        }
        _audit_appointment(state, db, action="appointment.fail", result=result)
        return {
            "current_step": "appointment",
            "appointment_result": result,
            "hitl_required": True,
            "hitl_reason": "Could not reschedule to an available slot",
        }

    # --- Book (default) ---
    dept_id = _department_id(state)
    if not dept_id:
        result = {
            "ok": False,
            "error": "missing_department",
            "message": "routing_result.department_id required to book",
        }
        _audit_appointment(state, db, action="appointment.fail", result=result)
        return {
            "current_step": "appointment",
            "appointment_result": result,
            "hitl_required": True,
            "hitl_reason": "No department to book against — needs staff routing",
        }

    available = get_available_slots(db, department_id=dept_id, limit=max(20, max_retries))
    slots = available.get("slots") or []
    if not slots:
        result = {
            "ok": False,
            "error": "no_slots",
            "message": "No available slots for department",
            "available_count": 0,
            "slots": [],
        }
        _audit_appointment(state, db, action="appointment.fail", result=result)
        return {
            "current_step": "appointment",
            "appointment_result": result,
            "hitl_required": True,
            "hitl_reason": "No available appointment slots",
        }

    book_reason = reason or (state.get("raw_request") or "")[:200] or None
    last_err = {}
    for slot in slots[:max_retries]:
        booked = book_appointment(
            state,
            db,
            slot_id=slot["slot_id"],
            reason=book_reason,
        )
        if booked.get("ok"):
            result = _from_booked(booked)
            result["available_count"] = available.get("count", len(slots))
            _audit_appointment(state, db, action="appointment.book", result=result)
            return {
                "current_step": "appointment",
                "appointment_result": result,
                "hitl_required": False,
                "hitl_reason": None,
            }
        last_err = booked
        if booked.get("error") not in _RETRYABLE:
            break

    result = {
        "ok": False,
        "error": last_err.get("error") or "booking_failed",
        "message": last_err.get("message") or "Could not book any tried slot",
        "available_count": available.get("count", len(slots)),
    }
    _audit_appointment(state, db, action="appointment.fail", result=result)
    return {
        "current_step": "appointment",
        "appointment_result": result,
        "hitl_required": True,
        "hitl_reason": "All slot booking attempts failed",
    }


def get_appointment_tools(state: GraphState, db: Session) -> list[StructuredTool]:
    """Bind appointment tools to the current workflow state + DB session."""

    def _slots(
        department_id: str = "",
        doctor_id: str = "",
        limit: int = 20,
    ) -> dict[str, Any]:
        return get_available_slots(
            db,
            department_id=department_id or _department_id(state) or None,
            doctor_id=doctor_id or None,
            limit=limit,
        )

    def _book(slot_id: str, reason: str = "") -> dict[str, Any]:
        return book_appointment(
            state,
            db,
            slot_id=slot_id,
            reason=reason or None,
        )

    def _cancel(appointment_id: str) -> dict[str, Any]:
        return cancel_appointment(state, db, appointment_id=appointment_id)

    def _reschedule(appointment_id: str, new_slot_id: str) -> dict[str, Any]:
        return reschedule_appointment(
            state,
            db,
            appointment_id=appointment_id,
            new_slot_id=new_slot_id,
        )

    return [
        StructuredTool.from_function(
            func=_slots,
            name="get_available_slots",
            description="List AVAILABLE slots for a department or doctor.",
        ),
        StructuredTool.from_function(
            func=_book,
            name="book_appointment",
            description="Book an AVAILABLE slot for the workflow patient.",
        ),
        StructuredTool.from_function(
            func=_cancel,
            name="cancel_appointment",
            description="Cancel an appointment and free its slot.",
        ),
        StructuredTool.from_function(
            func=_reschedule,
            name="reschedule_appointment",
            description="Move an appointment to a new AVAILABLE slot.",
        ),
    ]
