"""Optional LLM clinical-intent classifier (PRD §11.1 stage 2).

Used only when keyword screen is inconclusive and use_llm=True.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger("agentcare.safety")


def llm_screen_request(text: str) -> dict[str, Any] | None:
    """
    Ask the LLM whether the request is clinical (blocked) vs administrative (allowed).

    Returns None if LLM unavailable; otherwise
    {safe, category, safe_alternative, stage: llm}.
    """
    try:
        from core.llm import get_llm

        llm = get_llm(temperature=0.0)
        prompt = f"""You are a healthcare administration safety filter.
Classify the patient message as SAFE (administrative only) or UNSAFE (clinical).

UNSAFE if it asks for diagnosis, prescription/dosage, or treatment recommendations.
SAFE if it is about appointments, documents, department routing, or hospital navigation.

Message:
\"\"\"{text}\"\"\"

Reply with ONLY JSON:
{{"safe": true/false, "category": "DIAGNOSIS"|"PRESCRIPTION"|"TREATMENT"|"ADMIN"|null, "safe_alternative": "short redirect message"}}
"""
        response = llm.invoke(prompt)
        raw = getattr(response, "content", None) or str(response)
        if isinstance(raw, list):
            raw = " ".join(str(x) for x in raw)
        data = _extract_json(str(raw))
        if data is None:
            return None
        return {
            "safe": bool(data.get("safe", True)),
            "category": data.get("category"),
            "safe_alternative": data.get("safe_alternative"),
            "flags": [] if data.get("safe", True) else [str(data.get("category") or "clinical").lower()],
            "stage": "llm",
        }
    except Exception as exc:
        logger.warning("LLM safety screen failed: %s", exc)
        return None


def _extract_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
