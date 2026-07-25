"""Safety agent node — rules first, optional LLM second (Phase 3.3).

Deterministic node for the LangGraph pipeline: calls safety tools directly and
returns a GraphState partial update. Also exposes LangChain StructuredTools for
optional LLM binding later.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from sqlalchemy.orm import Session

from agents.prompts import load_prompt
from core.graph_state import GraphState, SafetyResult
from tools.safety_tools import (
    block_unsafe_action,
    create_escalation,
    screen_request,
    write_audit_event,
)

SAFETY_PROMPT = load_prompt("safety")


def safety_node(
    state: GraphState,
    db: Session,
    *,
    use_llm: bool = False,
) -> GraphState:
    """
    Screen ``raw_request`` and escalate when unsafe.

    Order (PRD): keyword/rules first via ``screen_request``; LLM only when
    ``use_llm=True``. On block, persist escalation + audit through tools.

    Does not commit — caller commits the session.
    """
    screen = screen_request(state, db, use_llm=use_llm)

    if screen.get("safe", True):
        safety_result: SafetyResult = {
            "safe": True,
            "flags": list(screen.get("flags") or []),
            "category": screen.get("category"),
            "reason": screen.get("reason") or "Administrative request — allowed",
            "stage": screen.get("stage") or "keywords",
            "blocked": False,
            "escalation_id": None,
            "safe_alternative": screen.get("safe_alternative"),
        }
        if screen.get("matched"):
            safety_result["matched"] = list(screen["matched"])
        if state.get("actor_user_id"):
            write_audit_event(
                state,
                db,
                action="safety.pass",
                entity_type="WorkflowRun",
                entity_id=state.get("workflow_run_id"),
                event_metadata={
                    "stage": safety_result.get("stage"),
                    "reason": safety_result.get("reason"),
                },
            )
        return {
            "current_step": "safety",
            "safety_result": safety_result,
            "hitl_required": False,
            "hitl_reason": None,
        }

    blocked = block_unsafe_action(state, db, screen_result=screen)
    esc = blocked.get("escalation") or {}
    escalation_id = esc.get("escalation_id")

    safety_result = {
        "safe": False,
        "flags": list(screen.get("flags") or []),
        "category": screen.get("category"),
        "reason": screen.get("reason") or "Clinical / unsafe request blocked",
        "stage": screen.get("stage") or "keywords",
        "blocked": True,
        "escalation_id": escalation_id,
        "safe_alternative": screen.get("safe_alternative") or blocked.get("message"),
        "message": blocked.get("message") or screen.get("safe_alternative"),
    }
    if screen.get("matched"):
        safety_result["matched"] = list(screen["matched"])

    reason = safety_result["reason"]
    return {
        "current_step": "safety",
        "safety_result": safety_result,
        "hitl_required": True,
        "hitl_reason": reason,
    }


def get_safety_tools(state: GraphState, db: Session) -> list[StructuredTool]:
    """
    Bind safety tools to the current workflow state + DB session.

    Suitable for ``llm.bind_tools(...)`` in a ReAct-style agent. The pipeline
    node itself uses the Python functions directly for deterministic tests.
    """

    def _screen(raw_request: str = "", use_llm: bool = False) -> dict[str, Any]:
        return screen_request(
            state,
            db,
            raw_request=raw_request or None,
            use_llm=use_llm,
        )

    def _escalate(reason: str) -> dict[str, Any]:
        return create_escalation(state, db, reason=reason)

    def _block(raw_request: str = "") -> dict[str, Any]:
        scoped: GraphState = {**state}
        if raw_request:
            scoped["raw_request"] = raw_request
        return block_unsafe_action(scoped, db)

    def _audit(
        action: str,
        entity_type: str,
        entity_id: str = "",
    ) -> dict[str, Any]:
        return write_audit_event(
            state,
            db,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id or None,
        )

    return [
        StructuredTool.from_function(
            func=_screen,
            name="screen_request",
            description=(
                "Screen a patient request for clinical / prescription / emergency "
                "language. Rules first; set use_llm=True for deeper LLM check."
            ),
        ),
        StructuredTool.from_function(
            func=_escalate,
            name="create_escalation",
            description="Create a staff escalation for the current workflow_run_id.",
        ),
        StructuredTool.from_function(
            func=_block,
            name="block_unsafe_action",
            description=(
                "If the request is unsafe, create escalation + audit and return "
                "the block payload. Prefer this when screening already failed."
            ),
        ),
        StructuredTool.from_function(
            func=_audit,
            name="write_audit_event",
            description="Append an audit event attributed to actor_user_id in state.",
        ),
    ]
