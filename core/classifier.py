"""3-stage document classification (PRD §14.3) — AgentCare admin document types.

Stage 1: filename regex (fast, free)
Stage 2: filename keyword heuristics (free)
Stage 3: optional LLM vision (Gemma) when enabled + API key present

EasyOCR is optional later; Stage 2 covers filename text for MVP tests.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from db.models import DocumentType

logger = logging.getLogger("agentcare.classifier")

# Stage 1 — most specific patterns first
_FILENAME_REGEX: list[tuple[str, str]] = [
    (r"ecg|ekg|electrocardiogram", DocumentType.ECG.value),
    (r"blood|cbc|lab[_-]?report|haemogram|hemogram", DocumentType.BLOOD_REPORT.value),
    (r"x[_-]?ray|mri|ct[_-]?scan|radiology|ultrasound|imaging", DocumentType.RADIOLOGY.value),
    (r"referral|refer[_-]?letter", DocumentType.REFERRAL_LETTER.value),
    (r"\bid[_-]?proof|aadhaar|aadhar|passport|pan[_-]?card|kyc", DocumentType.ID_PROOF.value),
    (r"discharge", DocumentType.DISCHARGE_SUMMARY.value),
]

# Stage 2 — looser keywords on filename (and optional extracted text)
_KEYWORD_HINTS: list[tuple[str, str]] = [
    (r"ecg|ekg", DocumentType.ECG.value),
    (r"blood|lab", DocumentType.BLOOD_REPORT.value),
    (r"scan|xray|x-ray|mri|ct\b", DocumentType.RADIOLOGY.value),
    (r"refer", DocumentType.REFERRAL_LETTER.value),
    (r"passport|aadhaar|license|licence", DocumentType.ID_PROOF.value),
    (r"discharge|summary", DocumentType.DISCHARGE_SUMMARY.value),
]


def classify_document_file(
    filename: str,
    *,
    text_hint: str | None = None,
    use_llm: bool = False,
    file_bytes: bytes | None = None,
) -> dict[str, Any]:
    """
    Classify a document into DocumentType.

    Returns: { document_type, stage, confidence, reason }
    """
    name = (filename or "").strip()
    name_l = name.lower()

    # --- Stage 1: regex on filename ---
    for pattern, doc_type in _FILENAME_REGEX:
        if re.search(pattern, name_l):
            return {
                "document_type": doc_type,
                "stage": 1,
                "confidence": 0.9,
                "reason": f"Filename regex matched /{pattern}/",
            }

    # --- Stage 2: keywords on filename (+ optional text hint) ---
    haystack = name_l
    if text_hint:
        haystack = f"{name_l} {text_hint.lower()}"
    for pattern, doc_type in _KEYWORD_HINTS:
        if re.search(pattern, haystack):
            return {
                "document_type": doc_type,
                "stage": 2,
                "confidence": 0.7,
                "reason": f"Keyword heuristic matched /{pattern}/",
            }

    # --- Stage 3: optional LLM (skipped unless explicitly requested) ---
    if use_llm and file_bytes is not None:
        llm_result = _classify_with_llm(filename, file_bytes)
        if llm_result is not None:
            return llm_result

    return {
        "document_type": DocumentType.UNKNOWN.value,
        "stage": 0,
        "confidence": 0.2,
        "reason": "No stage matched; classified as UNKNOWN",
    }


def _classify_with_llm(filename: str, file_bytes: bytes) -> dict[str, Any] | None:
    """Stage 3 — best-effort Gemma vision; returns None on failure."""
    try:
        import base64

        from langchain_core.messages import HumanMessage

        from core.llm import get_llm

        llm = get_llm(prefer_google=True, temperature=0.0)
        b64 = base64.b64encode(file_bytes).decode("ascii")
        # Assume image/jpeg unless PDF — vision models vary; keep simple for MVP
        mime = "application/pdf" if filename.lower().endswith(".pdf") else "image/jpeg"
        types = ", ".join(t.value for t in DocumentType)
        prompt = (
            "Classify this medical-administration document into exactly ONE type:\n"
            f"{types}\n"
            "Reply with ONLY the type name."
        )
        msg = HumanMessage(
            content=[
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": f"data:{mime};base64,{b64}"},
            ]
        )
        response = llm.invoke([msg])
        raw = (response.content or "").strip().upper().replace(" ", "_")
        for t in DocumentType:
            if t.value in raw or raw == t.value:
                return {
                    "document_type": t.value,
                    "stage": 3,
                    "confidence": 0.8,
                    "reason": f"LLM classified as {t.value}",
                }
        return {
            "document_type": DocumentType.UNKNOWN.value,
            "stage": 3,
            "confidence": 0.4,
            "reason": f"LLM returned unrecognized label: {raw[:80]}",
        }
    except Exception as exc:
        logger.warning("Stage-3 LLM classify failed: %s", exc)
        return None
