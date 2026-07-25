"""LangGraph pipeline assembly (Phase 4.1–4.3).

Wires Phase 3 nodes into a StateGraph with conditional edges and HITL
``staff_review`` via ``interrupt()``. Durable checkpoints use SqliteSaver
at ``data/checkpoints.db`` (Phase 4.3); tests may pass MemorySaver or a temp path.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Literal

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from agents.appointment_node import appointment_node
from agents.coordinator_node import coordinator_finalize, coordinator_init
from agents.document_node import document_node
from agents.followup_node import followup_node
from agents.routing_node import routing_node
from agents.safety_node import safety_node
from agents.staff_review_node import infer_hitl_source, staff_review_node
from core.config import get_settings
from core.graph_state import GraphState

RouteAfterSafety = Literal["routing", "staff_review"]
RouteAfterRouting = Literal["appointment", "staff_review"]
RouteAfterAppointment = Literal["document", "staff_review"]
RouteAfterStaff = Literal["appointment", "document", "coordinator_finalize"]


def route_after_safety(state: GraphState) -> RouteAfterSafety:
    """Unsafe or HITL after safety → staff_review; else continue to routing."""
    safety = state.get("safety_result") or {}
    if safety.get("safe") is False or safety.get("blocked") or state.get("hitl_required"):
        return "staff_review"
    return "routing"


def route_after_routing(state: GraphState) -> RouteAfterRouting:
    """Low-confidence routing → staff_review; else appointment."""
    routing = state.get("routing_result") or {}
    if routing.get("needs_staff_review") or state.get("hitl_required"):
        return "staff_review"
    return "appointment"


def route_after_appointment(state: GraphState) -> RouteAfterAppointment:
    """Booking failure needing staff → staff_review; else document."""
    appt = state.get("appointment_result") or {}
    if state.get("hitl_required") and not appt.get("ok"):
        return "staff_review"
    return "document"


def route_after_staff_review(state: GraphState) -> RouteAfterStaff:
    """
    After HITL resume:
      - safety → always finalize (no clinical booking)
      - reject → finalize
      - routing approve → appointment
      - appointment approve → document
    """
    source = state.get("hitl_source") or infer_hitl_source(state)
    decision = (state.get("staff_decision") or {}).get("decision", "reject")

    if source == "safety":
        return "coordinator_finalize"
    if decision != "approve":
        return "coordinator_finalize"
    if source == "routing":
        routing = state.get("routing_result") or {}
        if not routing.get("department_id"):
            return "coordinator_finalize"
        return "appointment"
    if source == "appointment":
        return "document"
    return "coordinator_finalize"


def build_graph(db: Session) -> StateGraph:
    """Construct an uncompiled StateGraph closed over ``db``."""

    def _init(state: GraphState) -> GraphState:
        return coordinator_init(state, db)

    def _safety(state: GraphState) -> GraphState:
        # USE_LLM (default true): keywords first, then LLM clinical screen.
        return safety_node(state, db, use_llm=get_settings().use_llm)

    def _routing(state: GraphState) -> GraphState:
        return routing_node(state, db)

    def _appointment(state: GraphState) -> GraphState:
        return appointment_node(state, db)

    def _document(state: GraphState) -> GraphState:
        # Optional Gemma vision classify when heuristics are inconclusive.
        return document_node(
            state, db, use_llm_classify=get_settings().use_llm
        )

    def _followup(state: GraphState) -> GraphState:
        return followup_node(state, db)

    def _staff(state: GraphState) -> GraphState:
        return staff_review_node(state, db)

    def _finalize(state: GraphState) -> GraphState:
        return coordinator_finalize(state, db)

    graph = StateGraph(GraphState)

    graph.add_node("coordinator_init", _init)
    graph.add_node("safety", _safety)
    graph.add_node("routing", _routing)
    graph.add_node("appointment", _appointment)
    graph.add_node("document", _document)
    graph.add_node("followup", _followup)
    graph.add_node("staff_review", _staff)
    graph.add_node("coordinator_finalize", _finalize)

    graph.add_edge(START, "coordinator_init")
    graph.add_edge("coordinator_init", "safety")

    graph.add_conditional_edges(
        "safety",
        route_after_safety,
        {"routing": "routing", "staff_review": "staff_review"},
    )
    graph.add_conditional_edges(
        "routing",
        route_after_routing,
        {"appointment": "appointment", "staff_review": "staff_review"},
    )
    graph.add_conditional_edges(
        "appointment",
        route_after_appointment,
        {"document": "document", "staff_review": "staff_review"},
    )
    graph.add_conditional_edges(
        "staff_review",
        route_after_staff_review,
        {
            "appointment": "appointment",
            "document": "document",
            "coordinator_finalize": "coordinator_finalize",
        },
    )

    graph.add_edge("document", "followup")
    graph.add_edge("followup", "coordinator_finalize")
    graph.add_edge("coordinator_finalize", END)

    return graph


@contextmanager
def get_checkpointer(path: str | None = None) -> Iterator[SqliteSaver]:
    """
    Open a durable SqliteSaver (PRD: ``data/checkpoints.db``).

    Use as a context manager so the SQLite connection is closed cleanly::

        with get_checkpointer() as saver:
            graph = compile_workflow(db, checkpointer=saver)
            ...
    """
    settings = get_settings()
    settings.ensure_data_dirs()
    db_path = path or settings.checkpoint_db_path
    with SqliteSaver.from_conn_string(db_path) as saver:
        saver.setup()
        yield saver


def compile_workflow(db: Session, *, checkpointer: Any | None = None) -> Any:
    """
    Compile the AgentCare workflow graph.

    Pass a SqliteSaver from ``get_checkpointer()`` for production / durable HITL.
    If omitted, uses MemorySaver (convenient for fast unit tests).
    """
    saver = checkpointer if checkpointer is not None else MemorySaver()
    return build_graph(db).compile(checkpointer=saver)
