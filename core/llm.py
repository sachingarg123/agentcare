"""LLM factory — Groq primary with Google-hosted Gemma fallback.

PRD §12 / 7.6: provider clients use ``max_retries`` with exponential backoff
so transient timeouts / rate limits don't fail the workflow on first blip.
"""

from __future__ import annotations

import logging
from typing import Any

from core.config import get_settings

logger = logging.getLogger("agentcare.llm")

# PRD §12 — retry with exponential backoff (handled inside LangChain clients)
LLM_MAX_RETRIES = 3


def get_llm(*, temperature: float = 0.0, prefer_google: bool = False) -> Any:
    """
    Return a chat model for agent nodes.

    - Default: Groq (`GROQ_MODEL`, e.g. qwen/qwen3-32b)
    - Fallback / vision: Google AI Studio Gemma (`GOOGLE_MODEL`, default gemma-4-31b-it)
      when Groq is unavailable or `prefer_google=True` (document Stage-3)

    Same Gemma wiring as MediShield / deepagent (`ChatGoogleGenerativeAI` + GOOGLE_API_KEY).
    Does not call the network at import time — only when an agent invokes the model.
    """
    settings = get_settings()

    if prefer_google:
        return _google_llm(settings, temperature=temperature)

    try:
        return _groq_llm(settings, temperature=temperature)
    except Exception as exc:  # construction / missing key
        logger.warning("Groq unavailable (%s); falling back to Gemma", exc)
        return _google_llm(settings, temperature=temperature)


def _groq_llm(settings, *, temperature: float):
    from langchain_groq import ChatGroq

    if not settings.groq_api_key:
        raise ValueError("GROQ_API_KEY is not set")

    return ChatGroq(
        api_key=settings.groq_api_key,
        model=settings.groq_model,
        temperature=temperature,
        max_retries=LLM_MAX_RETRIES,
    )


def _google_llm(settings, *, temperature: float):
    from langchain_google_genai import ChatGoogleGenerativeAI

    if not settings.google_api_key:
        raise ValueError(
            "GOOGLE_API_KEY is not set (needed for Gemma fallback / document vision)"
        )

    return ChatGoogleGenerativeAI(
        model=settings.google_model,
        google_api_key=settings.google_api_key,
        temperature=temperature,
        max_retries=LLM_MAX_RETRIES,
    )
