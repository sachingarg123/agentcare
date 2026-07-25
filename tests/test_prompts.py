"""Phase 3.2 — six distinct agent system prompts are loadable."""

from __future__ import annotations

import pytest

from agents.prompts import PROMPT_NAMES, PromptNotFoundError, list_prompts, load_prompt


def test_all_six_prompts_exist_and_are_nonempty():
    paths = list_prompts()
    assert set(paths) == set(PROMPT_NAMES)
    assert len(PROMPT_NAMES) == 6

    bodies: dict[str, str] = {}
    for name in PROMPT_NAMES:
        text = load_prompt(name)
        assert len(text) > 100, f"{name} prompt too short"
        assert (
            "PulseDesk" in text
            or "AgentCare" in text
            or "administrative" in text.lower()
        )
        bodies[name] = text

    # Genuinely distinct — no two files identical
    assert len(set(bodies.values())) == 6


def test_each_prompt_states_admin_boundary():
    for name in PROMPT_NAMES:
        text = load_prompt(name).lower()
        assert any(
            phrase in text
            for phrase in (
                "administrative",
                "never diagnose",
                "no clinical",
                "clinical advice",
                "hard boundaries",
            )
        ), f"{name} missing admin/clinical boundary language"


def test_load_prompt_unknown_raises():
    with pytest.raises(PromptNotFoundError):
        load_prompt("billing")
