"""Routing tools — department lookup + administrative intent classification (PRD 2.2).

Used by the Routing agent after safety passes:
  lookup_departments → classify_intent → (later) appointment booking for that dept.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from core.graph_state import GraphState
from db.repositories import DepartmentRepository

# Administrative intents (not clinical diagnosis)
INTENT_BOOK = "BOOK_APPOINTMENT"
INTENT_RESCHEDULE = "RESCHEDULE_APPOINTMENT"
INTENT_CANCEL = "CANCEL_APPOINTMENT"
INTENT_UPLOAD = "UPLOAD_DOCUMENT"
INTENT_FOLLOWUP = "FOLLOWUP_VISIT"
INTENT_GENERAL = "GENERAL_ADMIN"

# Keyword hints → department name (must match seeded Department.name)
_DEPARTMENT_KEYWORDS: dict[str, list[str]] = {
    "Cardiology": [
        "cardiology",
        "cardiac",
        "heart",
        "ecg",
        "ekg",
        "echo",
        "chest pain follow",
    ],
    "Radiology": [
        "radiology",
        "x-ray",
        "xray",
        "mri",
        "ct scan",
        "ultrasound",
        "imaging",
    ],
    "Orthopedics": [
        "orthopedic",
        "orthopedics",
        "bone",
        "fracture",
        "knee",
        "joint",
        "spine",
    ],
    "Dermatology": [
        "dermatology",
        "skin",
        "rash",
        "acne",
        "dermatologist",
    ],
    "General Medicine": [
        "general medicine",
        "general physician",
        "physician",
        "primary care",
        "family doctor",
        "gp",
        "checkup",
        "check-up",
        "check up",
        "medical checkup",
        "medical check",
        "health checkup",
        "health check",
        "routine check",
        "physical exam",
        "fever",
        "flu",
        "cold",
        "cough",
    ],
}

_INTENT_KEYWORDS: list[tuple[str, list[str]]] = [
    (INTENT_CANCEL, ["cancel", "call off"]),
    (INTENT_RESCHEDULE, ["reschedule", "postpone", "move my appointment", "change slot"]),
    (INTENT_UPLOAD, ["attach", "upload", "document", "report", "ecg file", "lab report"]),
    (INTENT_FOLLOWUP, ["follow-up", "follow up", "followup", "review visit"]),
    (
        INTENT_BOOK,
        [
            "book",
            "appointment",
            "schedule",
            "see a doctor",
            "visit",
            "consult",
            "consultation",
        ],
    ),
]


def lookup_departments(db: Session) -> list[dict[str, Any]]:
    """
    Return active departments from SQLite (real SELECT — not a fixed string).

    Read-only; no patient scope check required.
    """
    repo = DepartmentRepository(db)
    depts = repo.list_active()
    return [
        {
            "department_id": d.id,
            "name": d.name,
            "description": d.description,
            "active": d.active,
        }
        for d in depts
    ]


def classify_intent(
    state: GraphState,
    db: Session,
    *,
    raw_request: str | None = None,
) -> dict[str, Any]:
    """
    Classify administrative intent and map to a department.

    Strategy (Phase 2 — deterministic, testable without LLM):
      1. Load departments from DB via lookup_departments
      2. Score department keywords against the request text
      3. Detect administrative intents (book / upload / …)
      4. Return structured result with confidence

    Routing agent (Phase 3) may later call an LLM and merge; low confidence
    (< 0.5) should trigger staff_review HITL.

    Does not write patient data — read-only classification.
    """
    text = (raw_request or state.get("raw_request") or "").strip()
    if not text:
        return {
            "intents": [INTENT_GENERAL],
            "department_id": None,
            "department_name": None,
            "confidence": 0.0,
            "reason": "Empty request; cannot classify",
            "needs_staff_review": True,
        }

    departments = lookup_departments(db)
    if not departments:
        return {
            "intents": [INTENT_GENERAL],
            "department_id": None,
            "department_name": None,
            "confidence": 0.0,
            "reason": "No active departments in database",
            "needs_staff_review": True,
        }

    text_l = text.lower()

    # --- Department scoring ---
    name_to_dept = {d["name"]: d for d in departments}
    scores: dict[str, float] = {d["name"]: 0.0 for d in departments}

    for name, keywords in _DEPARTMENT_KEYWORDS.items():
        if name not in scores:
            continue
        for kw in keywords:
            if kw in text_l:
                scores[name] += 1.0
        # Exact department name mention
        if name.lower() in text_l:
            scores[name] += 2.0

    best_name = max(scores, key=scores.get)
    best_score = scores[best_name]

    # Confidence: normalize by a soft cap
    if best_score <= 0:
        department = None
        confidence = 0.2
        reason = "No department keywords matched; defaulting to low confidence"
    else:
        department = name_to_dept[best_name]
        # 1 hit → ~0.55, 2+ → higher, capped at 0.95
        confidence = min(0.95, 0.4 + 0.2 * best_score)
        matched = [
            kw
            for kw in _DEPARTMENT_KEYWORDS.get(best_name, [])
            if kw in text_l
        ]
        if best_name.lower() in text_l:
            matched = [best_name.lower(), *matched]
        reason = f"Matched department '{best_name}' via: {', '.join(matched) or 'score'}"

    # --- Intent detection (multi-label) ---
    intents: list[str] = []
    for intent, keywords in _INTENT_KEYWORDS:
        if any(kw in text_l for kw in keywords):
            intents.append(intent)
    if not intents:
        intents = [INTENT_GENERAL]

    # Follow-up + department often implies booking
    if INTENT_FOLLOWUP in intents and INTENT_BOOK not in intents:
        intents.insert(0, INTENT_BOOK)

    needs_staff_review = confidence < 0.5 or department is None

    return {
        "intents": intents,
        "department_id": department["department_id"] if department else None,
        "department_name": department["name"] if department else None,
        "confidence": round(confidence, 2),
        "reason": reason,
        "needs_staff_review": needs_staff_review,
        "raw_request": text,
    }
