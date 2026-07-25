# AgentCare — Product Requirements Document (PRD)

**Version:** 1.2 (RBAC + eval scope locked)  
**Author:** Sachin  
**Date:** 2026-07-22  
**Repository:** `~/Documents/AI Learning/agentcare`  
**Stack decision:** Python · LangGraph · FastAPI · SQLite · Groq (+ Google fallback) · LangSmith · SMTP  
**Reference projects:** `multimodal-ai` (MediShield), `agent-ai-langraph`, `hr-assist`, `Email Agent`

---

## 1. Executive Summary

**AgentCare** is an agentic healthcare **administration** system that coordinates a patient's non-clinical journey — registration, department routing, appointment booking, document collection, reminders, and follow-up — while keeping all medical decisions under human supervision.

The system uses a **LangGraph multi-agent orchestrator** where a Coordinator delegates to specialized agents (Routing, Appointment, Document, Follow-up, Safety). Each agent has its own system prompt and tool set. All workflow state, appointments, documents, escalations, and audit events are persisted in **SQLite**.

> **Explicit non-goal:** AgentCare does not diagnose, prescribe, recommend dosages, or replace clinicians. Clinical language in patient requests triggers the Safety Agent and human escalation.

---

## 2. Problem Statement

Hospital administrative workflows are fragmented across forms, phone calls, spreadsheets, and disconnected systems. Staff spend time on repetitive tasks:

- Patient registration and profile updates
- Routing requests to the correct department
- Checking doctor availability and booking/rescheduling appointments
- Collecting and organizing medical documents (ECG, lab reports, referrals)
- Sending reminders and scheduling follow-ups
- Escalating emergencies or ambiguous requests to humans

AgentCare automates the **administrative** coordination while preserving human oversight for sensitive or uncertain cases.

---

## 3. Goals & Non-Goals

### 3.1 Goals

| ID | Goal |
|----|------|
| G1 | End-to-end administrative workflow: registration → intent → routing → booking → documents → confirmation → reminder → follow-up |
| G2 | ≥ 6 genuinely distinct LangGraph agent nodes with separate prompts and/or tools |
| G3 | ≥ 8 functional tools that read/write persisted data (not hardcoded responses) |
| G4 | Backend-enforced RBAC: `PATIENT` / `STAFF` / `ADMIN` + object-level ownership checks |
| G11 | E2E workflow tests + minimal `evaluate.py` harness (routing + safety) in MVP |
| G5 | Human escalation/approval workflow with persisted `Escalation` records |
| G6 | Full audit trail (`AuditEvent`) for every agent action and staff override |
| G7 | Working patient + staff UI wired to real backend logic |
| G8 | LangSmith tracing for agent observability |
| G9 | Synthetic seed data; no real PHI in repo |
| G10 | Pass hackathon automated checks (Python, LLM client, CI workflow) |

### 3.2 Non-Goals (v1)

- Clinical diagnosis, prescription, or treatment recommendations
- Insurance billing, bed allocation, pharmacy, OT scheduling
- FHIR integration (optional extension)
- Production-grade HIPAA compliance (use synthetic data only)
- Mobile native apps

---

## 4. User Personas & Roles

### 4.1 Patient (`role: PATIENT`)

| Capability | Description |
|------------|-------------|
| Register / update profile | Name, DOB, phone, language, emergency contact |
| Submit administrative request | Natural-language request (e.g. "cardiology appointment next week + attach ECG") |
| Book / reschedule / cancel | Appointment lifecycle via agent workflow |
| Upload documents | ECG, blood reports, referrals, ID proof |
| View status | Workflow progress, appointment details, document checklist, reminders |

### 4.2 Hospital Staff (`role: STAFF`)

| Capability | Description |
|------------|-------------|
| View all patient requests | Filter by status, department, escalation |
| Review escalations | Approve/reject sensitive actions; add notes |
| View audit history | Per-workflow and per-patient action trail (read-only) |
| Override agent decisions | HITL resume via LangGraph `interrupt` (staff approval node) |

**Cannot:** CRUD departments, doctors, or appointment slots (ADMIN only).

### 4.3 Hospital Administrator (`role: ADMIN`)

| Capability | Description |
|------------|-------------|
| Everything `STAFF` can do | Escalations, HITL, view all requests |
| Manage reference data | CRUD departments, doctors, appointment slots |
| View full audit log | Same as staff; may add admin-only filters later |

Seed data includes at least: 2 `PATIENT`, 1 `STAFF`, 1 `ADMIN` users.

### 4.4 RBAC Enforcement (two layers)

**Layer 1 — Route-level:** `require_role(...)` on every endpoint.

```python
@router.get("/staff/escalations")
def list_escalations(user: User = Depends(require_role("STAFF", "ADMIN"))): ...

@router.post("/staff/departments")
def create_department(user: User = Depends(require_role("ADMIN"))): ...
```

**Layer 2 — Object-level:** After loading a resource, verify the caller owns it or has staff/admin access.

```python
# auth/ownership.py
def assert_patient_owns_workflow(user: User, workflow: WorkflowRun) -> None:
    patient = patient_repo.get_by_user_id(user.id)
    if workflow.patient_id != patient.id:
        raise HTTPException(403, "Not your workflow")

def assert_patient_owns_appointment(user: User, appointment: Appointment) -> None:
    patient = patient_repo.get_by_user_id(user.id)
    if appointment.patient_id != patient.id:
        raise HTTPException(403, "Not your appointment")
```

**Object-level rules (mandatory):**

| Resource | PATIENT | STAFF | ADMIN |
|----------|---------|-------|-------|
| `WorkflowRun` | Own `patient_id` only | All | All |
| `Appointment` | Own `patient_id` only | All (read) | All |
| `PatientDocument` | Own `patient_id` only | All (read) | All |
| `Reminder` | Own `patient_id` only | All (read) | All |
| `Department` / `Doctor` / `Slot` | No access | Read only (via agent) | CRUD |
| `Escalation` | No access | Resolve | Resolve |
| `AuditEvent` | No access | Read | Read |

**Layer 3 — Agent/tool scope:** `actor_user_id` + `actor_role` in `GraphState`; tools reject cross-patient writes (see §6.4, §7.1).

**Audit rule:** Any staff/admin action on behalf of a patient writes `AuditEvent` with `actor_id = current_user.id`.

---

## 5. Core User Journey

### 5.1 Happy Path Example

**Patient input:**
> "I need a cardiology follow-up next week and want to attach my old ECG."

| Step | Agent | Action |
|------|-------|--------|
| 1 | Coordinator | Parse request; ensure patient profile exists; create `WorkflowRun` |
| 2 | Safety | Screen for clinical/diagnosis/prescription language; pass or escalate |
| 3 | Routing | Classify intent → `CARDIOLOGY_FOLLOWUP`; map to Cardiology department |
| 4 | Appointment | Query available Cardiology slots next week; book slot; persist `Appointment` |
| 5 | Document | Classify uploaded ECG; store metadata + checksum; map to patient; check duplicates |
| 6 | Follow-up | Create appointment reminder (24h before); schedule post-visit follow-up task |
| 7 | Coordinator | Aggregate results; return confirmation from DB records; write `AuditEvent` trail |

### 5.2 Escalation Paths

| Trigger | Safety / Routing behavior |
|---------|---------------------------|
| "I have chest pain, what medicine should I take?" | Safety blocks → `Escalation` → staff review |
| Unknown department / low routing confidence | Routing escalates → staff assigns department |
| Slot conflict on booking | Appointment retries alternate slot or escalates |
| Duplicate document (same checksum) | Document flags duplicate; coordinator informs patient |
| Missing required doc for department | Document returns checklist; workflow pauses for upload |

---

## 6. Agent Architecture (LangGraph)

### 6.1 Design Principles (from your MediShield + LangGraph learnings)

1. **Typed `GraphState`** — single TypedDict contract passed between all nodes (`core/graph_state.py`)
2. **Sequential where dependencies exist** — Appointment before Follow-up; Safety early in pipeline
3. **Coordinator owns orchestration** — specialist agents do not call each other directly
4. **Tools are thin service wrappers** — agents invoke tools; tools hit repositories/DB
5. **LangGraph checkpointer** — `SqliteSaver` at `data/checkpoints.db` for workflow resume + HITL
6. **Agent context in state** — every workflow carries `actor_user_id` + `actor_role`; tools enforce patient scope
6. **LangSmith** — `LANGCHAIN_TRACING_V2=true` for all agent runs

### 6.2 Agent Inventory

| Agent | Node name | Model | Distinct prompt | Tools |
|-------|-----------|-------|-----------------|-------|
| **Coordinator** | `coordinator` | Groq `qwen/qwen3-32b` | Plans workflow, delegates, aggregates final response | `get_patient_profile`, `create_workflow_run`, `update_workflow_state`, `write_audit_event` |
| **Safety & Escalation** | `safety` | Groq + rule-based | Blocks diagnosis/prescription; detects emergency keywords | `screen_request`, `create_escalation`, `block_unsafe_action` |
| **Department Routing** | `routing` | Groq | Classifies administrative intent; maps to department | `lookup_departments`, `classify_intent`, `create_escalation` |
| **Appointment** | `appointment` | Groq | Slot search, conflict check, book/reschedule/cancel | `get_available_slots`, `book_appointment`, `reschedule_appointment`, `cancel_appointment` |
| **Document** | `document` | Groq/Google + 3-stage pipeline | Regex → filename/OCR → LLM classify; store, dedupe, missing-doc check | `classify_document`, `store_document`, `check_duplicates`, `get_required_documents` |
| **Follow-up** | `followup` | Groq/Google + SMTP | Reminders and post-visit tasks; real email delivery | `create_reminder`, `schedule_followup`, `send_notification` |

> **Minimum 3 distinct agents:** satisfied with 6. Each has a unique system prompt in `agents/prompts/`.

### 6.3 LangGraph State Machine

```
START
  │
  ▼
coordinator_init          ← create/load patient, WorkflowRun, parse raw request
  │
  ▼
safety_screen             ← block clinical requests → escalation short-circuit
  │
  ├── [UNSAFE] ──────────────────────────────► staff_review (interrupt) ──► END
  │
  ▼
routing                   ← department + intent
  │
  ├── [LOW_CONFIDENCE] ─────────────────────► staff_review (interrupt) ──► resume
  │
  ▼
appointment               ← availability + booking
  │
  ├── [NEEDS_APPROVAL] ─────────────────────► staff_review (interrupt) ──► resume
  │
  ▼
document                  ← classify + store (skip if no files)
  │
  ▼
followup                  ← reminder + follow-up task
  │
  ▼
coordinator_finalize      ← confirmation from DB, audit trail
  │
  ▼
END
```

**HITL pattern** (from `agent-ai-langraph/02_langraph_agent`): `staff_review` node uses `interrupt()`; staff UI calls `POST /workflows/{id}/resume` with `Command(resume={...})`.

### 6.4 GraphState Contract (draft)

```python
class GraphState(TypedDict, total=False):
    # Identity — set by API when workflow starts; flows through all nodes/tools
    workflow_run_id: str
    patient_id: str                     # subject of the workflow (whose appointment/docs)
    actor_user_id: str                  # who triggered the request (audit + tool scope)
    actor_role: str                     # "PATIENT" | "STAFF" | "ADMIN"
    raw_request: str
    uploaded_files: list[dict]          # {filename, bytes, mime_type}

    # Coordinator
    administrative_intents: list[str]   # e.g. ["BOOK_APPOINTMENT", "UPLOAD_DOCUMENT"]
    current_step: str

    # Safety
    safety_result: SafetyResult         # {safe: bool, flags: [], escalation_id?: str}

    # Routing
    routing_result: RoutingResult       # {department_id, department_name, confidence, reason}

    # Appointment
    appointment_result: AppointmentResult  # {appointment_id, slot_id, status, doctor_name}

    # Document
    document_result: DocumentResult       # {stored: [], duplicates: [], missing: []}

    # Follow-up
    followup_result: FollowupResult       # {reminder_ids: [], followup_task_id}

    # Final
    confirmation: ConfirmationResult      # assembled from persisted records
    error: str | None
```

---

## 7. Tools & Service Layer

### 7.1 Tool Design Rules

- Tools live in `tools/` and are registered via `@tool` decorator or explicit LangChain `StructuredTool`
- Each tool calls a **repository** in `db/` — never raw SQL in agent nodes
- Tools return structured JSON (Pydantic models)
- **No fixed success strings** — outcomes depend on DB state
- **Scope enforcement:** Tools that write patient data must validate against `GraphState`:

```python
# tools/_scope.py — called at start of mutating tools
def assert_tool_scope(state: GraphState, target_patient_id: str) -> None:
  if state["patient_id"] != target_patient_id:
      raise PermissionError("Tool cannot act on a different patient")
  if state["actor_role"] == "PATIENT":
      # patient actor must match workflow patient (via user_id → patient_id lookup)
      patient = patient_repo.get_by_user_id(state["actor_user_id"])
      if patient.id != state["patient_id"]:
          raise PermissionError("Patient cannot run workflow for another patient")
```

- `write_audit_event` always records `actor_id=state["actor_user_id"]` and `metadata.role=state["actor_role"]`

### 7.2 Tool Catalog

| Tool | Used by | Real logic |
|------|---------|------------|
| `get_or_create_patient` | Coordinator | Upsert `PatientProfile` by user_id |
| `lookup_departments` | Routing | SELECT active departments |
| `classify_intent` | Routing | LLM structured output → intent enum |
| `get_available_slots` | Appointment | Query `AppointmentSlot` with date/doctor filters |
| `book_appointment` | Appointment | Transaction: lock slot, create `Appointment`, update slot status |
| `reschedule_appointment` | Appointment | Release old slot, book new |
| `cancel_appointment` | Appointment | Set status cancelled, free slot |
| `classify_document` | Document | Filename heuristics → LLM fallback (MediShield 3-stage pattern) |
| `store_document` | Document | Save file to `data/uploads/`, SHA-256 checksum, insert `PatientDocument` |
| `check_document_duplicates` | Document | Compare checksum per patient |
| `get_required_documents` | Document | Department-specific checklist from config table |
| `create_reminder` | Follow-up | Insert `Reminder` with scheduled_at |
| `schedule_followup` | Follow-up | Insert follow-up task linked to appointment |
| `send_notification` | Follow-up | SMTP email to patient/staff + log to `notifications` table |
| `screen_request` | Safety | Keyword rules + LLM clinical-intent classifier |
| `create_escalation` | Safety, Routing | Insert `Escalation` record |
| `write_audit_event` | All | Insert `AuditEvent` |

---

## 8. Data Model

SQLite via SQLAlchemy 2.0 + Alembic migrations.

| Entity | Key fields |
|--------|------------|
| `User` | id, name, email, password_hash, role (`PATIENT` \| `STAFF` \| `ADMIN`), created_at |
| `PatientProfile` | id, user_id, date_of_birth, phone, preferred_language, emergency_contact |
| `Department` | id, name, description, active |
| `Doctor` | id, department_id, name, active |
| `AppointmentSlot` | id, doctor_id, start_time, end_time, status (`AVAILABLE` \| `BOOKED` \| `BLOCKED`) |
| `Appointment` | id, patient_id, doctor_id, slot_id, status, reason, created_at |
| `PatientDocument` | id, patient_id, document_type, file_path, document_date, checksum |
| `WorkflowRun` | id, patient_id, current_step, state (JSON), status, created_at |
| `Reminder` | id, patient_id, appointment_id, reminder_type, scheduled_at, status |
| `Escalation` | id, workflow_run_id, reason, status, reviewed_by, created_at |
| `AuditEvent` | id, actor_id, action, entity_type, entity_id, metadata (JSON), created_at |
| `DepartmentDocumentRequirement` | department_id, document_type, required (bool) |

**Workflow state persistence:** `WorkflowRun.state` stores serialized `GraphState` snapshot after each node; LangGraph checkpointer stores thread state for HITL resume.

---

## 9. API Design

Base: `FastAPI` at `/api/v1`

### 9.1 Auth

| Method | Path | Role | Description |
|--------|------|------|-------------|
| POST | `/auth/register` | public | Create patient account |
| POST | `/auth/login` | public | JWT token |
| GET | `/auth/me` | any | Current user + profile |

### 9.2 Patient

| Method | Path | Role | Object-level check |
|--------|------|------|-------------------|
| POST | `/requests` | PATIENT | Sets `patient_id` from `current_user` profile |
| GET | `/requests/{id}` | PATIENT | `workflow.patient_id == own patient` |
| GET | `/appointments` | PATIENT | Filter `WHERE patient_id = own` |
| POST | `/appointments/{id}/cancel` | PATIENT | `appointment.patient_id == own` |
| GET | `/documents` | PATIENT | Filter `WHERE patient_id = own` |
| POST | `/documents/upload` | PATIENT | Store under own `patient_id` |
| GET | `/reminders` | PATIENT | Filter `WHERE patient_id = own` |

### 9.3 Staff & Admin

| Method | Path | Role | Notes |
|--------|------|------|-------|
| GET | `/staff/requests` | STAFF, ADMIN | All workflow runs |
| GET | `/staff/escalations` | STAFF, ADMIN | Pending escalations |
| POST | `/staff/escalations/{id}/resolve` | STAFF, ADMIN | Approve/reject + resume graph |
| POST | `/workflows/{id}/resume` | STAFF, ADMIN | LangGraph `Command(resume=...)` |
| GET | `/staff/audit` | STAFF, ADMIN | Audit log with filters |
| CRUD | `/staff/departments`, `/staff/doctors`, `/staff/slots` | **ADMIN only** | Reference data management |

### 9.4 Real-time (optional, from MediShield pattern)

| Method | Path | Description |
|--------|------|-------------|
| WS | `/ws/workflows/{id}` | Stream agent completion events |

---

## 10. User Interface

**Approach:** FastAPI serves static HTML (Tailwind CDN) — same pattern as MediShield.

| Page | Path | Audience |
|------|------|----------|
| Login / Register | `/` | All |
| Patient Dashboard | `/patient` | Submit request, view status, upload docs |
| Workflow Detail | `/patient/workflows/{id}` | Agent step progress, confirmation |
| Staff Dashboard | `/staff` | Request queue, escalations |
| Escalation Review | `/staff/escalations/{id}` | Approve/reject with reason |
| Admin | `/staff/admin` | Departments, doctors, slots (**ADMIN role only**) |

UI must call real APIs — no hardcoded mock data in templates.

---

## 11. Safety & Compliance

### 11.1 Healthcare Safety Boundary

**Blocked intents (rule + LLM):**
- Diagnosis requests ("Do I have diabetes?")
- Prescription / dosage requests ("What medicine should I take?")
- Treatment recommendations ("Should I get surgery?")

**Allowed intents:**
- Appointment booking, rescheduling, cancellation
- Department routing for follow-up visits
- Document upload and administrative coordination
- General hospital navigation ("Where is radiology?")

**Implementation:**
1. `safety/keywords.py` — fast regex blocklist
2. `safety/llm_classifier.py` — structured output: `{is_clinical: bool, category, safe_alternative}`
3. On block → create `Escalation`, skip booking agents, return message directing patient to call emergency line if urgent

### 11.2 Data Safety

- Synthetic patients only in seed data
- `.env` gitignored; `.env.example` with placeholders
- No real API keys in repo
- File uploads stored locally under `data/uploads/` (gitignored)

---

## 12. Error Handling & Recovery

| Scenario | Behavior |
|----------|----------|
| LLM timeout / rate limit | Retry with exponential backoff (`max_retries=3`) |
| Slot race condition | DB transaction + optimistic lock; retry next slot |
| Partial workflow failure | `WorkflowRun.status = FAILED`; state preserved for manual resume |
| HITL timeout | Escalation remains `PENDING`; staff can resume anytime |
| Document upload failure | Rollback metadata insert; return actionable error |

---

## 13. Observability & Evaluation

### 13.1 LangSmith (runtime observability)

```env
LANGCHAIN_TRACING_V2=true
LANGCHAIN_PROJECT=agentcare
LANGSMITH_API_KEY=...
```

Trace each agent node as a named run with `workflow_run_id`, `patient_id`, `actor_role` metadata.

### 13.2 Automated testing — three layers (MVP)

| Layer | What | Where | Phase |
|-------|------|-------|-------|
| **Unit** | Tools, repos, agents, RBAC helpers | `tests/test_*.py` | 1–3 |
| **E2E** | Full workflow via API or graph invoke | `tests/test_workflow_e2e.py`, `tests/test_api_e2e.py` | 4, 5, 7 |
| **Eval harness** | Batch scoring on labeled fixtures | `evaluate.py` + `eval/fixtures/` | 7 |

### 13.3 E2E test scope (required — Phase 4 + 7)

**`tests/test_workflow_e2e.py`** — graph-level, no HTTP:

| Case | Assert |
|------|--------|
| Happy path: cardiology + ECG upload | `WorkflowRun.status=COMPLETED`, `Appointment` row exists, document stored |
| Safety block: prescription request | `Escalation` created, no `Appointment` booked |
| HITL: low-confidence routing | Graph pauses at `staff_review`; resume completes workflow |
| Object scope: wrong `patient_id` in state | Tool raises `PermissionError` |

**`tests/test_api_e2e.py`** — HTTP via `TestClient` (Phase 7):

| Case | Assert |
|------|--------|
| Patient submits request → GET status | 200, confirmation from DB |
| Patient A cannot GET Patient B's workflow | **403** |
| STAFF can GET any workflow | 200 |
| STAFF cannot POST `/staff/departments` | **403** |
| ADMIN can CRUD department | 201 |
| Clinical trap via API | 200 with escalation; no appointment |

Uses seeded users; `SMTP_DISABLED=true`; optional LLM mock for CI stability.

### 13.4 Minimal eval harness (required — Phase 7)

Inspired by MediShield `evaluate.py` — **slim MVP scope** (routing + safety only; document/appointment evals deferred).

```
eval/
├── fixtures/
│   ├── routing_cases.jsonl      # 15 cases: {request, expected_department}
│   └── safety_cases.jsonl       # 10 cases: {request, expected_safe: bool}
├── evaluate.py                  # CLI: python evaluate.py [--routing] [--safety]
└── report_template.html         # Simple HTML summary (no heavy charts)
```

**`evaluate.py` behaviour:**
1. Load fixtures from `eval/fixtures/`
2. For routing: invoke routing agent/node only → compare `department_name` to expected
3. For safety: invoke safety node only → compare `safe` flag to expected
4. Print summary table + write `docs/eval_report.html`
5. Exit code 1 if safety recall < 100% or routing accuracy < 80% (CI gate)

| Metric | Fixture count | MVP target |
|--------|---------------|------------|
| Routing accuracy | 15 | ≥ 80% |
| Safety recall | 10 clinical traps | **100%** (zero misses allowed) |
| E2E happy path | 1 (in pytest) | Pass |
| API RBAC E2E | 5 (in pytest) | Pass |

**Optional Phase 8 extension:** expand to 20+ routing cases, document classification eval, appointment booking eval, calibration charts (full MediShield-style report).

### 13.5 CI integration

```yaml
# In pytest job
pytest tests/ -v

# Optional separate eval job (uses real LLM — run on main only)
python evaluate.py --routing --safety
```

---

## 14. Tech Stack

| Layer | Choice | Rationale |
|-------|--------|-----------|
| Language | Python 3.12+ | Hackathon requirement |
| Agent framework | LangGraph | Your proven pattern in MediShield + coursework |
| LLM (primary) | Groq `qwen/qwen3-32b` | Fast, used in agent-ai-langraph |
| LLM (fallback) | Google `gemini-2.0-flash` | MediShield pattern; used on Groq rate-limit/errors |
| LLM integration | `langchain-groq` + `langchain-google-genai` | Unified via `core/llm.py` factory |
| Checkpointer | `langgraph.checkpoint.sqlite.SqliteSaver` | `data/checkpoints.db` — durable HITL resume |
| Notifications | SMTP (real email) | Appointment confirmations + reminders |
| API | FastAPI + Uvicorn | MediShield, Email Agent, hr-assist |
| ORM | SQLAlchemy 2.0 | Standard; works with Alembic |
| Database | SQLite (`data/agentcare.db`) | Simple local dev; meets requirement |
| Auth | JWT (python-jose) + bcrypt | Lightweight RBAC |
| Package manager | uv | Your standard across projects |
| Testing | pytest | Email Agent, multimodal-ai patterns |
| CI | `.github/workflows/agentcare-checks.yml` | Hackathon requirement |

### 14.1 LLM Fallback Strategy

`core/llm.py` exposes a single `get_llm()` used by all agent nodes:

```python
# Pseudocode — primary Groq, fallback Google on retriable errors
try:
    return ChatGroq(model="qwen/qwen3-32b", max_retries=2, ...)
except (RateLimitError, APIConnectionError, ...):
    return ChatGoogleGenerativeAI(model="gemini-2.0-flash", max_retries=3, ...)
```

- **Primary:** Groq for all agent calls (low latency, cost-effective)
- **Fallback triggers:** HTTP 429, 5xx, connection timeout, Groq `RateLimitError`
- **Document vision (Stage 3):** Google Gemma/Gemini multimodal when file is image/PDF (same as MediShield)
- **LangSmith:** Tag runs with `llm_provider=groq|google` for observability

### 14.2 SMTP Email Notifications

Real email delivery via Python `smtplib` + `email.mime` in `services/email_service.py`.

**Supported providers (via `.env`):**

| Provider | SMTP host | Notes |
|----------|-----------|-------|
| Gmail | `smtp.gmail.com:587` | Requires [App Password](https://myaccount.google.com/apppasswords) (2FA enabled) |
| Outlook | `smtp.office365.com:587` | App password or OAuth (app password simpler for hackathon) |
| SendGrid | `smtp.sendgrid.net:587` | API key as password; good for demos |

**Email types sent by Follow-up Agent:**

| Type | Recipient | Trigger |
|------|-----------|---------|
| `APPOINTMENT_CONFIRMATION` | Patient | After successful booking |
| `APPOINTMENT_REMINDER` | Patient | 24h before appointment (`Reminder.scheduled_at`) |
| `DOCUMENT_REQUEST` | Patient | Missing required documents for department |
| `ESCALATION_ALERT` | Staff | Safety block or low-confidence routing |
| `FOLLOWUP_TASK` | Patient | Post-visit follow-up scheduled |

**Safety rules:**
- Only send to email addresses on file (`User.email` / `PatientProfile`)
- Never include clinical advice in email body — administrative content only
- Log every send attempt to `notifications` table (`status: SENT | FAILED`)
- Tests use `SMTP_DISABLED=true` or pytest fixture with `aiosmtpd` debug server

**`.env.example` additions:**

```env
# LLM
GROQ_API_KEY=
GOOGLE_API_KEY=

# SMTP (Gmail example)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-agentcare@gmail.com
SMTP_PASSWORD=your-app-password
SMTP_FROM=AgentCare <your-agentcare@gmail.com>
SMTP_TLS=true
```

### 14.3 Document Classification — 3-Stage Pipeline

Reuses MediShield `core/classifier.py` pattern:

| Stage | Method | Cost | Latency |
|-------|--------|------|---------|
| 1 | Filename regex | $0 | <1ms |
| 2 | Filename keywords + optional EasyOCR text scan | $0 | 1–2s |
| 3 | LLM multimodal (Google fallback vision) | ~$0.01 | 2–5s |

Document types: `ECG`, `BLOOD_REPORT`, `RADIOLOGY`, `REFERRAL_LETTER`, `ID_PROOF`, `DISCHARGE_SUMMARY`, `UNKNOWN`

---

## 15. Project Structure

**Root path:** `/Users/sachinga@backbase.com/Documents/AI Learning/agentcare`

```
agentcare/
├── main.py                      # FastAPI app, routes, lifespan, static files
├── pyproject.toml               # uv dependencies
├── .env.example
├── .gitignore
├── README.md
├── alembic/                     # DB migrations
├── core/
│   ├── config.py                # pydantic-settings
│   ├── llm.py                   # Groq primary + Google fallback factory
│   ├── classifier.py            # 3-stage document classification
│   ├── graph_state.py           # TypedDict contracts
│   └── pipeline.py              # LangGraph assembly + SqliteSaver
├── agents/
│   ├── prompts/                 # One .md or .py per agent
│   ├── coordinator_node.py
│   ├── safety_node.py
│   ├── routing_node.py
│   ├── appointment_node.py
│   ├── document_node.py
│   └── followup_node.py
├── tools/
│   ├── _scope.py                # assert_tool_scope from GraphState
│   └── ...                      # patient, routing, appointment, etc.
├── services/
│   ├── workflow_service.py
│   └── email_service.py         # SMTP notification delivery
├── db/
│   ├── models.py
│   ├── session.py
│   └── repositories/            # One repo per entity
├── safety/
│   ├── keywords.py
│   └── classifier.py
├── auth/
│   ├── jwt.py
│   ├── dependencies.py          # get_current_user, require_role
│   └── ownership.py             # object-level assert_* helpers
├── eval/
│   ├── fixtures/
│   │   ├── routing_cases.jsonl
│   │   └── safety_cases.jsonl
│   └── report_template.html
├── evaluate.py                  # Minimal routing + safety eval harness
├── scripts/
│   └── seed_data.py             # 2 patients, 1 staff, 1 admin, hospital data
├── static/                      # HTML/CSS/JS UI
├── data/                        # gitignored: agentcare.db, checkpoints.db, uploads
├── tests/
│   ├── test_tools.py
│   ├── test_agents.py
│   ├── test_rbac.py
│   ├── test_object_access.py    # Patient A ≠ Patient B 403 tests
│   ├── test_safety.py
│   ├── test_workflow_e2e.py     # Graph-level E2E
│   └── test_api_e2e.py          # HTTP E2E + RBAC matrix
└── .github/workflows/
    └── agentcare-checks.yml
```

---

## 16. Success Criteria (Definition of Done)

- [ ] Patient can register, submit request, see workflow status with real DB-backed confirmation
- [ ] Staff can view escalations and approve/reject (HITL resume works)
- [ ] 6 agent nodes execute in LangGraph with distinct prompts
- [ ] ≥ 8 tools invoked during a single happy-path workflow
- [ ] Safety agent blocks ≥ 1 clinical test prompt and creates escalation
- [ ] Audit log shows full trail for a workflow run
- [ ] RBAC tests: route-level (patient ≠ staff endpoints) **and** object-level (patient A ≠ patient B resources)
- [ ] ADMIN can CRUD reference data; STAFF cannot
- [ ] `actor_user_id` + `actor_role` passed into every workflow; tools reject cross-patient writes
- [ ] E2E: happy path + safety block + HITL resume (`test_workflow_e2e.py`, `test_api_e2e.py`)
- [ ] Minimal eval harness: `python evaluate.py` passes routing (≥80%) + safety (100% recall)
- [ ] `pytest` passes; CI workflow green
- [ ] README with architecture diagram and setup steps
- [ ] No hardcoded agent responses masquerading as dynamic results

---

## 17. Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Scope creep (billing, insurance) | Strict v1 scope in this PRD |
| LLM hallucinating appointments | Confirmation always built from DB query post-booking |
| Parallel agent state bugs (learned from MediShield) | Sequential graph; document after appointment |
| Time pressure | Phase implementation (see task list); MVP at Phase 5 |
| Groq rate limits | Auto-fallback to Google Gemini via `core/llm.py` |
| SMTP deliverability / spam | Use dedicated sender; plain-text + HTML; seed emails to own inbox for demo |
| Real email in tests | `SMTP_DISABLED=true` in test env; optional `aiosmtpd` debug server |

---

## 18. Locked Decisions

| Decision | Choice | Date |
|----------|--------|------|
| Repository location | `~/Documents/AI Learning/agentcare` | 2026-07-22 |
| LLM provider | **Groq primary** + **Google fallback** | 2026-07-22 |
| Document classification | **3-stage pipeline** (regex → OCR/keywords → LLM) | 2026-07-22 |
| Notifications | **Real SMTP email** (Gmail App Password or SendGrid) | 2026-07-22 |
| LangGraph checkpointer | **`SqliteSaver`** at `data/checkpoints.db` | 2026-07-22 |
| RBAC roles | **`PATIENT` / `STAFF` / `ADMIN`** (three roles) | 2026-07-22 |
| Object-level access | **Mandatory** on all patient-scoped resources | 2026-07-22 |
| Agent context | **`actor_user_id` + `actor_role`** in GraphState | 2026-07-22 |
| Eval scope (MVP) | **E2E pytest + minimal `evaluate.py`** (routing + safety) | 2026-07-22 |

**Still open (optional):**
- Deployment URL for judges (Railway/Render) — defer to Phase 8

---

# Implementation Task List

Phased execution. Each task is independently testable. Estimated effort in person-days (solo).

---

## Phase 0 — Project Bootstrap (Day 1)

| # | Task | Output | Depends on |
|---|------|--------|------------|
| 0.1 | Create `~/Documents/AI Learning/agentcare` with `pyproject.toml` (langgraph, langchain-groq, langchain-google-genai, fastapi, sqlalchemy, alembic, python-jose, passlib, pytest, aiosmtpd) | Installable project | — |
| 0.2 | Add `.env.example`, `.gitignore`, folder structure per §15 | Scaffold | 0.1 |
| 0.3 | `core/config.py` — DB path, JWT, Groq + Google keys, LangSmith, SMTP settings | Config module | 0.1 |
| 0.6 | `core/llm.py` — Groq primary + Google fallback factory with retry | LLM client | 0.3 |
| 0.4 | Download hackathon CI workflow → `.github/workflows/agentcare-checks.yml` | CI ready | 0.1 |
| 0.5 | README skeleton (setup, architecture placeholder) | Docs stub | 0.1 |

**Exit criteria:** `uv sync` works; `python -c "import main"` passes; CI file present.

---

## Phase 1 — Database & Auth (Day 1–2)

| # | Task | Output | Depends on |
|---|------|--------|------------|
| 1.1 | SQLAlchemy models for all entities (§8) | `db/models.py` | 0.2 |
| 1.2 | Alembic init + initial migration | `alembic/versions/001_initial.py` | 1.1 |
| 1.3 | Repository layer: `user_repo`, `patient_repo`, `department_repo`, `doctor_repo`, `slot_repo`, `appointment_repo`, `document_repo`, `workflow_repo`, `reminder_repo`, `escalation_repo`, `audit_repo` | `db/repositories/` | 1.2 |
| 1.4 | `scripts/seed_data.py` — 5 departments, 10 doctors, 50 slots, 2 patients, 1 staff, 1 admin | Seed script | 1.3 |
| 1.5 | JWT auth: `get_current_user`, `require_role("STAFF","ADMIN")`, `require_role("ADMIN")` | `auth/dependencies.py` | 1.3 |
| 1.5b | `auth/ownership.py` — `assert_patient_owns_workflow`, `assert_patient_owns_appointment`, etc. | Object-level guards | 1.3 |
| 1.6 | Tests: `test_rbac.py` (route-level), `test_object_access.py` (patient A ≠ B) | Passing tests | 1.5, 1.5b |

**Exit criteria:** Seed includes 3 roles; login returns JWT with role claim; patient A gets 403 on patient B's workflow.

---

## Phase 2 — Tools & Services (Day 2–3)

| # | Task | Output | Depends on |
|---|------|--------|------------|
| 2.0 | `tools/_scope.py` — `assert_tool_scope(state, target_patient_id)` | Tool RBAC | 1.5b, 3.1 |
| 2.1 | Patient tool: `get_or_create_patient` | `tools/patient_tools.py` | 1.3, 2.0 |
| 2.2 | Routing tools: `lookup_departments`, `classify_intent` | `tools/routing_tools.py` | 1.3, 2.0 |
| 2.3 | Appointment tools: `get_available_slots`, `book_appointment`, `reschedule_appointment`, `cancel_appointment` | `tools/appointment_tools.py` | 1.3 |
| 2.4 | Document tools: `classify_document`, `store_document`, `check_document_duplicates`, `get_required_documents` | `tools/document_tools.py` | 1.3 |
| 2.5 | Follow-up tools: `create_reminder`, `schedule_followup`, `send_notification` | `tools/followup_tools.py` | 1.3 |
| 2.5b | `services/email_service.py` — SMTP send with HTML templates; `SMTP_DISABLED` for tests | Email service | 0.3 |
| 2.4b | `core/classifier.py` — 3-stage doc classify (port from MediShield) | Classifier | 0.6 |
| 2.6 | Safety tools: `screen_request`, `create_escalation`, `write_audit_event` | `tools/safety_tools.py` | 1.3 |
| 2.7 | Tests: `test_tools.py` — each tool with real DB, no mocks for DB | Passing tests | 2.1–2.6 |
| 2.8 | Tests: `test_email_service.py` — aiosmtpd debug server captures outbound mail | Email tests | 2.5b |

**Exit criteria:** Each tool reads/writes SQLite; booking tool handles slot conflicts; duplicate checksum detected; SMTP sends real email when configured.

---

## Phase 3 — Agent Nodes (Day 3–4)

| # | Task | Output | Depends on |
|---|------|--------|------------|
| 3.1 | `core/graph_state.py` — all TypedDict contracts incl. `actor_user_id`, `actor_role` | State types | 0.2 |
| 3.2 | Agent prompts in `agents/prompts/*.md` (6 distinct system messages) | Prompt files | — |
| 3.3 | `safety_node.py` — rules first, LLM second; bind safety tools | Agent node | 2.6, 3.1 |
| 3.4 | `routing_node.py` — intent + department mapping | Agent node | 2.2, 3.1 |
| 3.5 | `appointment_node.py` — slot search + book with retry | Agent node | 2.3, 3.1 |
| 3.6 | `document_node.py` — 3-stage classify (regex → filename → LLM) | Agent node | 2.4, 3.1 |
| 3.7 | `followup_node.py` — reminder + follow-up task | Agent node | 2.5, 3.1 |
| 3.8 | `coordinator_node.py` — init + finalize nodes | Agent node | 2.1, 2.6, 3.1 |
| 3.9 | Tests: `test_agents.py` — unit test each node with fixture state | Passing tests | 3.3–3.8 |

**Exit criteria:** Each node returns structured state update; safety node blocks clinical prompt in test.

---

## Phase 4 — LangGraph Pipeline (Day 4–5)

| # | Task | Output | Depends on |
|---|------|--------|------------|
| 4.1 | `core/pipeline.py` — `StateGraph`, conditional edges, `route_after_safety`, `route_after_routing` | Compiled graph | 3.3–3.8 |
| 4.2 | `staff_review` node with `interrupt()` for HITL | HITL node | 4.1 |
| 4.3 | Checkpointer: `SqliteSaver` at `data/checkpoints.db` | Persistent resume | 4.1 |
| 4.4 | Workflow service: inject `actor_user_id` + `actor_role` into initial state on `start_workflow()` | `services/workflow_service.py` | 4.1–4.3 |
| 4.5 | LangSmith metadata tags (`workflow_run_id`, `patient_id`, `actor_role`) | Tracing | 4.1 |
| 4.6 | `tests/test_workflow_e2e.py` — happy path, safety block, HITL resume, tool scope | Graph E2E | 4.4 |

**Exit criteria:** Full graph runs end-to-end; escalation short-circuits booking; HITL resume continues graph.

---

## Phase 5 — API Layer (Day 5–6)

| # | Task | Output | Depends on |
|---|------|--------|------------|
| 5.1 | `main.py` lifespan: DB init, seed if empty | App bootstrap | 1.4, 4.4 |
| 5.2 | Auth routes: `/auth/register`, `/auth/login`, `/auth/me` | Auth API | 1.5 |
| 5.3 | Patient routes with object-level checks via `auth/ownership.py` | Patient API | 4.4, 1.5b |
| 5.4 | Staff routes (`STAFF`, `ADMIN`); escalations + resume | Staff API | 4.4, 1.5 |
| 5.5 | Admin routes: CRUD departments, doctors, slots — **`ADMIN` only** | Admin API | 1.5 |
| 5.6 | Audit route: `GET /staff/audit` | Audit API | 1.3 |
| 5.7 | Optional: WebSocket `/ws/workflows/{id}` for agent progress events | Real-time UI | 4.4 |

**Exit criteria:** curl/httpx can run full workflow; route + object-level RBAC enforced; STAFF gets 403 on admin CRUD.

---

## Phase 6 — User Interface (Day 6–7)

| # | Task | Output | Depends on |
|---|------|--------|------------|
| 6.1 | `static/login.html` — login/register | Auth UI | 5.2 |
| 6.2 | `static/patient.html` — submit request form + file upload | Patient UI | 5.3 |
| 6.3 | `static/workflow.html` — agent step timeline, confirmation card | Status UI | 5.3, 5.7 |
| 6.4 | `static/staff.html` — request queue + escalation list | Staff UI | 5.4 |
| 6.5 | `static/escalation.html` — review form (approve/reject) → calls resume API | HITL UI | 5.4 |
| 6.6 | `static/admin.html` — manage departments/doctors/slots (hide if not ADMIN) | Admin UI | 5.5 |
| 6.7 | Manual UX test against seed data | Demo script | 6.1–6.6 |

**Exit criteria:** Patient submits request in browser; staff resolves escalation; confirmation shows DB data.

---

## Phase 7 — Safety, Eval, Audit & Hardening (Day 7)

| # | Task | Output | Depends on |
|---|------|--------|------------|
| 7.1 | `test_safety.py` — 10 clinical trap prompts → escalation | Safety unit tests | 3.3 |
| 7.2 | `tests/test_api_e2e.py` — HTTP E2E: happy path, RBAC matrix, clinical trap | API E2E | 5.3–5.5 |
| 7.3 | `eval/fixtures/` — 15 routing + 10 safety labeled cases | Eval fixtures | 3.3, 3.4 |
| 7.4 | `evaluate.py` — batch routing + safety scoring, `docs/eval_report.html` | Eval harness | 7.3 |
| 7.5 | Verify every agent action writes `AuditEvent` with `actor_id` | Audit completeness | 4.4 |
| 7.6 | Error handling pass: LLM retries, workflow FAILED state | Resilience | 4.4 |
| 7.7 | Final README: architecture, RBAC matrix, eval instructions, demo walkthrough | Complete docs | All |
| 7.8 | Push + verify GitHub Actions `agentcare-checks.yml` green | CI pass | 0.4 |

**Exit criteria:** `pytest` green; `python evaluate.py` passes; safety recall 100%; routing ≥ 80%; eval report generated.

---

## Phase 8 — Optional Enhancements (post-MVP)

> **Not scored** (hackathon tie-breakers). Must not come at the expense of core requirements (Phases 0–7).  
> Core HITL (approve/reject + routing department override) remains in Phases 4–6; Phase 8 deepens staff actions.

### 8.A — Richer HITL staff actions (PulseDesk / AgentCare extension)

Source-aware escalation resolve UI + resume payloads (beyond generic approve/reject):

| # | Task | Notes |
|---|------|-------|
| 8.A.1 | **Safety approve/reject** | Keep non-clinical boundary: never auto-book from a clinical trap. Approve = mark handled + staff note / optional “safe admin follow-up” ticket; reject = close blocked. Confirm graph still ends without appointment. |
| 8.A.2 | **Routing approve** | Staff assigns department (done in MVP); optional: also assign preferred doctor / intent override before resume → appointment. |
| 8.A.3 | **Appointment approve** | Explicit staff choices: (a) **Book selected AVAILABLE slot**, (b) **Continue without booking** (current MVP), (c) **Reject / end**. Resume payload includes `slot_id` when booking. |
| 8.A.4 | Escalation detail API/UI | Show per-source action forms (safety / routing / appointment) with slot picker when `hitl_source=appointment`. |
| 8.A.5 | Audit | Log which HITL action path was taken (`escalation_approve_book_slot`, `escalation_continue_without_booking`, etc.). |

### 8.B — Product / platform extensions (hackathon optional list)

May be considered by human reviewers as tie-breakers:

| # | Task | Notes |
|---|------|-------|
| 8.B.1 | Multilingual / voice interaction | Hindi–English requests; optional Whisper voice input |
| 8.B.2 | Insurance eligibility pre-check | Out of core admin MVP; optional pre-visit check |
| 8.B.3 | Grievance management | Complaint intake + staff queue |
| 8.B.4 | Billing explanation | Plain-language bill summaries (non-payment processor) |
| 8.B.5 | Analytics dashboard | Volume, escalation rate, department load |
| 8.B.6 | Bed availability | Ward/bed inventory (non-goal for MVP) |
| 8.B.7 | Staff scheduling | Roster beyond appointment slots |
| 8.B.8 | FHIR-compatible integration | Optional external EHR bridge |
| 8.B.9 | MCP-based hospital tool server | Tools over MCP |
| 8.B.10 | Advanced agent observability | Beyond LangSmith basics |
| 8.B.11 | Consent management | Explicit consent records for data/actions |
| 8.B.12 | Accessibility features | WCAG-oriented UI hardening |

### 8.C — Eval / demo ops

| # | Task | Notes |
|---|------|-------|
| 8.C.1 | Expand `evaluate.py` — document classification + appointment booking metrics | Full MediShield-style report |
| 8.C.2 | Deploy to Railway/Render | Demo URL for judges |

---

## Suggested Implementation Order (Critical Path)

```
Phase 0 → Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5 → Phase 6 → Phase 7
                ↘ seed data early so agents have data to query
```

**MVP milestone (minimum viable demo):** End of Phase 5 — API-only demo with curl.  
**Submission milestone:** End of Phase 7 — UI + tests + CI.

---

## Mapping to Hackathon Scoring Criteria

| Criterion | PRD section | Implementation phase |
|-----------|-------------|---------------------|
| 3+ distinct agents | §6.2 | Phase 3–4 |
| 3+ functional tools | §7.2 | Phase 2 |
| Persistent SQL DB | §8 | Phase 1 |
| Workflow state | §6.4, WorkflowRun | Phase 4 |
| RBAC backend | §4.4, §9 | Phase 1, 5 |
| Object-level access | §4.4, `auth/ownership.py` | Phase 1, 5 |
| Agent tool scope | §6.4, §7.1, `tools/_scope.py` | Phase 2, 4 |
| E2E + eval harness | §13.3, §13.4 | Phase 4, 7 |
| Human escalation | §5.2, HITL | Phase 4, 6 |
| Audit logging | §7.2 `write_audit_event` | Phase 2, 7 |
| Document coordination | §7.2 document tools | Phase 2, 3.6 |
| Safety boundary | §11 | Phase 3.3, 7.1 |
| UI | §10 | Phase 6 |
| No hardcoded responses | §7.1 | All phases |

---

*End of PRD v1.2 — RBAC + eval scope locked; ready for Phase 0 implementation*
