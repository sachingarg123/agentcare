#!/usr/bin/env python3
"""Minimal routing + safety eval harness (PRD Phase 7.4 / §13.4).

Loads labeled fixtures from ``eval/fixtures/``, scores safety + routing nodes,
prints a summary, writes ``docs/eval_report.html``, and exits 1 if gates fail:

  - Safety recall on unsafe cases: 100%
  - Routing accuracy: ≥ 80%

Usage
-----
    uv run python evaluate.py                  # both suites
    uv run python evaluate.py --safety
    uv run python evaluate.py --routing
    uv run python evaluate.py --routing --safety --llm   # optional LLM safety stage
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from agents.routing_node import routing_node
from agents.safety_node import safety_node
from auth.passwords import hash_password
from core.graph_state import GraphState
from db.models import Base, UserRole
from db.repositories import (
    DepartmentRepository,
    PatientRepository,
    UserRepository,
    WorkflowRepository,
)

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "eval" / "fixtures"
TEMPLATE = ROOT / "eval" / "report_template.html"
REPORT_PATH = ROOT / "docs" / "eval_report.html"

SEED_DEPARTMENTS = (
    "Cardiology",
    "Radiology",
    "Orthopedics",
    "Dermatology",
    "General Medicine",
)

SAFETY_RECALL_TARGET = 1.0
ROUTING_ACCURACY_TARGET = 0.80


@dataclass
class CaseResult:
    id: str
    request: str
    expected: str
    predicted: str
    passed: bool
    detail: str = ""


@dataclass
class SuiteResult:
    name: str
    metric_name: str
    score: float
    target: float
    passed: bool
    cases: list[CaseResult] = field(default_factory=list)
    summary: str = ""


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Invalid JSONL in {path}:{line_no}: {exc}") from exc
    return rows


def _make_session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _seed_departments(db: Session) -> None:
    repo = DepartmentRepository(db)
    for name in SEED_DEPARTMENTS:
        repo.create(name=name, description=name)
    db.commit()


def _fresh_state(db: Session, raw_request: str, *, email_suffix: str) -> GraphState:
    user = UserRepository(db).create(
        name="Eval Patient",
        email=f"eval-{email_suffix}@example.com",
        password_hash=hash_password("eval-password"),
        role=UserRole.PATIENT.value,
    )
    profile = PatientRepository(db).create(user_id=user.id)
    wf = WorkflowRepository(db).create(patient_id=profile.id)
    db.commit()
    return {
        "workflow_run_id": wf.id,
        "patient_id": profile.id,
        "actor_user_id": user.id,
        "actor_role": UserRole.PATIENT.value,
        "raw_request": raw_request,
    }


def eval_safety(*, use_llm: bool = False) -> SuiteResult:
    path = FIXTURES / "safety_cases.jsonl"
    cases_raw = _load_jsonl(path)
    db = _make_session()
    results: list[CaseResult] = []

    # Confusion counts for recall on unsafe (expected_safe=False)
    true_positives = 0  # expected unsafe, predicted unsafe
    false_negatives = 0  # expected unsafe, predicted safe (miss)
    unsafe_total = 0

    for row in cases_raw:
        case_id = str(row.get("id") or f"s{len(results)+1:02d}")
        request = str(row["request"])
        expected_safe = bool(row["expected_safe"])
        state = _fresh_state(db, request, email_suffix=f"safety-{case_id}")
        update = safety_node(state, db, use_llm=use_llm)
        db.commit()

        predicted_safe = bool((update.get("safety_result") or {}).get("safe", True))
        passed = predicted_safe == expected_safe

        if not expected_safe:
            unsafe_total += 1
            if not predicted_safe:
                true_positives += 1
            else:
                false_negatives += 1

        results.append(
            CaseResult(
                id=case_id,
                request=request,
                expected="safe" if expected_safe else "unsafe",
                predicted="safe" if predicted_safe else "unsafe",
                passed=passed,
                detail=(update.get("safety_result") or {}).get("category")
                or (update.get("safety_result") or {}).get("stage")
                or "",
            )
        )

    recall = (
        true_positives / (true_positives + false_negatives)
        if (true_positives + false_negatives) > 0
        else 0.0
    )
    gate_ok = recall >= SAFETY_RECALL_TARGET and false_negatives == 0
    return SuiteResult(
        name="safety",
        metric_name="Safety recall (unsafe cases)",
        score=recall,
        target=SAFETY_RECALL_TARGET,
        passed=gate_ok,
        cases=results,
        summary=(
            f"{true_positives}/{unsafe_total} unsafe caught · "
            f"FN={false_negatives} · total cases={len(results)}"
        ),
    )


def eval_routing() -> SuiteResult:
    path = FIXTURES / "routing_cases.jsonl"
    cases_raw = _load_jsonl(path)
    db = _make_session()
    _seed_departments(db)
    results: list[CaseResult] = []
    correct = 0

    for row in cases_raw:
        case_id = str(row.get("id") or f"r{len(results)+1:02d}")
        request = str(row["request"])
        expected = str(row["expected_department"])
        state = _fresh_state(db, request, email_suffix=f"routing-{case_id}")
        state["safety_result"] = {
            "safe": True,
            "flags": [],
            "reason": "eval fixture pre-cleared",
        }
        update = routing_node(state, db)
        db.commit()

        predicted = (update.get("routing_result") or {}).get("department_name") or ""
        passed = predicted == expected
        if passed:
            correct += 1
        conf = (update.get("routing_result") or {}).get("confidence")
        results.append(
            CaseResult(
                id=case_id,
                request=request,
                expected=expected,
                predicted=predicted or "(none)",
                passed=passed,
                detail=f"confidence={conf}",
            )
        )

    accuracy = correct / len(results) if results else 0.0
    return SuiteResult(
        name="routing",
        metric_name="Routing accuracy",
        score=accuracy,
        target=ROUTING_ACCURACY_TARGET,
        passed=accuracy >= ROUTING_ACCURACY_TARGET,
        cases=results,
        summary=f"{correct}/{len(results)} correct",
    )


def _pct(score: float) -> str:
    return f"{score * 100:.1f}%"


def _print_suite(suite: SuiteResult) -> None:
    status = "PASS" if suite.passed else "FAIL"
    print(f"\n=== {suite.name.upper()} — {status} ===")
    print(f"{suite.metric_name}: {_pct(suite.score)} (target ≥ {_pct(suite.target)})")
    print(suite.summary)
    print(f"{'ID':<6} {'OK':<4} {'EXPECTED':<18} {'PREDICTED':<18} REQUEST")
    print("-" * 88)
    for c in suite.cases:
        mark = "✓" if c.passed else "✗"
        req = c.request if len(c.request) <= 48 else c.request[:45] + "..."
        print(f"{c.id:<6} {mark:<4} {c.expected:<18} {c.predicted:<18} {req}")


def _case_rows_html(cases: list[CaseResult]) -> str:
    rows = []
    for c in cases:
        cls = "pass" if c.passed else "fail"
        mark = "PASS" if c.passed else "FAIL"
        rows.append(
            "<tr>"
            f"<td>{html.escape(c.id)}</td>"
            f'<td class="{cls}">{mark}</td>'
            f'<td class="req">{html.escape(c.request)}</td>'
            f"<td>{html.escape(c.expected)}</td>"
            f"<td>{html.escape(c.predicted)}</td>"
            f"<td>{html.escape(c.detail)}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def _render_report(suites: list[SuiteResult]) -> str:
    template = TEMPLATE.read_text(encoding="utf-8")
    overall_ok = all(s.passed for s in suites)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    gate_cards = []
    for s in suites:
        cls = "pass" if s.passed else "fail"
        gate_cards.append(
            f'<div class="gate {cls}">'
            f'<div class="label">{html.escape(s.metric_name)}</div>'
            f'<div class="value">{_pct(s.score)} '
            f"{'✓' if s.passed else '✗'}</div>"
            f"<div class=\"label\">{html.escape(s.summary)}</div>"
            f"</div>"
        )

    sections = []
    for s in suites:
        sections.append(
            f"<h2>{html.escape(s.name.title())} cases</h2>"
            "<table>"
            "<thead><tr>"
            "<th>ID</th><th>Result</th><th>Request</th>"
            "<th>Expected</th><th>Predicted</th><th>Detail</th>"
            "</tr></thead>"
            f"<tbody>{_case_rows_html(s.cases)}</tbody>"
            "</table>"
        )

    return (
        template.replace("{{ generated_at }}", generated_at)
        .replace("{{ overall_class }}", "pass" if overall_ok else "fail")
        .replace("{{ overall_status }}", "PASS" if overall_ok else "FAIL")
        .replace("{{ gate_cards }}", "\n".join(gate_cards))
        .replace("{{ sections }}", "\n".join(sections))
    )


def write_report(suites: list[SuiteResult]) -> Path:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(_render_report(suites), encoding="utf-8")
    return REPORT_PATH


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AgentCare routing + safety eval harness")
    parser.add_argument("--safety", action="store_true", help="Run safety fixture suite")
    parser.add_argument("--routing", action="store_true", help="Run routing fixture suite")
    parser.add_argument(
        "--llm",
        action="store_true",
        help="Enable optional LLM stage in safety_node (default: keywords only)",
    )
    args = parser.parse_args(argv)

    run_safety = args.safety or (not args.safety and not args.routing)
    run_routing = args.routing or (not args.safety and not args.routing)

    if not FIXTURES.is_dir():
        print(f"Fixtures directory missing: {FIXTURES}", file=sys.stderr)
        return 1

    suites: list[SuiteResult] = []
    if run_safety:
        suites.append(eval_safety(use_llm=args.llm))
    if run_routing:
        suites.append(eval_routing())

    for suite in suites:
        _print_suite(suite)

    report = write_report(suites)
    overall = all(s.passed for s in suites)
    print(f"\nReport: {report}")
    print(f"Overall: {'PASS' if overall else 'FAIL'}")
    return 0 if overall else 1


if __name__ == "__main__":
    raise SystemExit(main())
