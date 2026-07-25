"""Fast clinical / prescription blocklist (PRD §11.1).

Rules run before any LLM — zero cost, high recall on obvious traps.
"""

from __future__ import annotations

import re
from typing import Any

# (category, pattern) — matched case-insensitively against raw request text
_BLOCK_PATTERNS: list[tuple[str, str]] = [
    (
        "DIAGNOSIS",
        r"\b(do i have|diagnose|diagnosis|what('s| is) wrong with me|is it cancer|"
        r"do i got|could it be)\b",
    ),
    (
        "PRESCRIPTION",
        r"\b(what medicine|which (medicine|drug|pill)|prescribe|prescription|"
        r"dosage|dose should i|how many mg|can i take|should i take)\b",
    ),
    (
        "TREATMENT",
        r"\b(should i (get|have) surgery|recommend (surgery|treatment)|"
        r"what treatment|treat my|is surgery)\b",
    ),
    (
        "EMERGENCY",
        r"\b(chest pain|can'?t breathe|cannot breathe|heart attack|"
        r"stroke|severe bleeding|unconscious)\b",
    ),
]

_COMPILED = [(cat, re.compile(pat, re.IGNORECASE)) for cat, pat in _BLOCK_PATTERNS]


def screen_keywords(text: str) -> dict[str, Any]:
    """
    Return {safe: bool, flags: [...], category?, matched?}.

    safe=False if any clinical/emergency pattern matches.
    """
    flags: list[str] = []
    matched: list[str] = []
    category: str | None = None

    for cat, cre in _COMPILED:
        m = cre.search(text or "")
        if m:
            flags.append(cat.lower())
            matched.append(m.group(0))
            if category is None:
                category = cat

    safe = len(flags) == 0
    return {
        "safe": safe,
        "flags": flags,
        "category": category,
        "matched": matched,
        "stage": "keywords",
    }
