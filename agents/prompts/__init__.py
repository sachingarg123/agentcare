"""Load agent system prompts from Markdown files (Phase 3.2)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent

PROMPT_NAMES = (
    "coordinator",
    "safety",
    "routing",
    "appointment",
    "document",
    "followup",
)


class PromptNotFoundError(KeyError):
    """Raised when a prompt name has no matching .md file."""


@lru_cache(maxsize=16)
def load_prompt(name: str) -> str:
    """
    Return the system prompt text for an agent.

    Args:
        name: One of PROMPT_NAMES (e.g. \"safety\" → safety.md).
    """
    key = name.strip().lower()
    path = _PROMPTS_DIR / f"{key}.md"
    if not path.is_file():
        raise PromptNotFoundError(
            f"Unknown prompt '{name}'. Expected one of: {', '.join(PROMPT_NAMES)}"
        )
    return path.read_text(encoding="utf-8").strip()


def list_prompts() -> dict[str, Path]:
    """Map prompt name → file path for all known agents."""
    return {name: _PROMPTS_DIR / f"{name}.md" for name in PROMPT_NAMES}
