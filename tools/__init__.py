"""Agent tools package — thin wrappers over repositories (Phase 2)."""

from tools._scope import ToolScopeError, assert_tool_scope
from tools.appointment_tools import (
    book_appointment,
    cancel_appointment,
    get_available_slots,
    reschedule_appointment,
)
from tools.document_tools import (
    check_document_duplicates,
    classify_document,
    get_required_documents,
    missing_documents,
    store_document,
)
from tools.followup_tools import create_reminder, schedule_followup, send_notification
from tools.patient_tools import get_or_create_patient
from tools.routing_tools import classify_intent, lookup_departments
from tools.safety_tools import (
    block_unsafe_action,
    create_escalation,
    screen_request,
    write_audit_event,
)

__all__ = [
    "ToolScopeError",
    "assert_tool_scope",
    "get_or_create_patient",
    "lookup_departments",
    "classify_intent",
    "get_available_slots",
    "book_appointment",
    "cancel_appointment",
    "reschedule_appointment",
    "classify_document",
    "store_document",
    "check_document_duplicates",
    "get_required_documents",
    "missing_documents",
    "create_reminder",
    "schedule_followup",
    "send_notification",
    "screen_request",
    "create_escalation",
    "write_audit_event",
    "block_unsafe_action",
]
