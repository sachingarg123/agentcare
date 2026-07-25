# Eval fixtures (Phase 7.3)

Labeled cases for `evaluate.py` (Phase 7.4). One JSON object per line.

| File | Count | Fields |
|------|-------|--------|
| `safety_cases.jsonl` | 10 | `id`, `request`, `expected_safe`, optional `category` |
| `routing_cases.jsonl` | 15 | `id`, `request`, `expected_department` |

**Safety:** 9 unsafe clinical traps + 1 safe admin request (for contrast).  
**Routing:** only safe administrative requests; `expected_department` must match seed names (`Cardiology`, `Radiology`, `Orthopedics`, `Dermatology`, `General Medicine`).

Do not put prescription/emergency wording in routing fixtures — those belong in safety.
