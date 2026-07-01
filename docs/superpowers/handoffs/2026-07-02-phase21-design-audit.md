# Phase 21 Strategic Dependency Graph — Design Audit Handoff

**Owner:** Codex / design audit  
**Status:** PASS — architecture approved for implementation  
**Branch:** `phase21-strategic-dependency-graph`  
**Plan:** `docs/superpowers/plans/2026-07-02-phase21-strategic-dependency-graph.md`

This document is the **hard contract** for Task 0 unit tests and all Grok implementation. Tests must encode these red lines; implementation must not weaken them.

---

## Verdict

**PASS.** The strategic dependency graph design is mature and robust. Grok is authorized to implement Tasks 1–10.

---

## P0 — Must be enforced in tests and code

| ID | Requirement | Test contract |
|---|---|---|
| P0-1 | Cross-project edges rejected | `ValueError("cross_project_dependency_rejected")` |
| P0-2 | Cycle creation rejected | `ValueError("roadmap_cycle_rejected")` |
| P0-3 | Legacy/corrupt cycles bounded in readiness | `evaluate_node_readiness` returns `blocked` with cycle finding, no hang |
| P0-4 | Markdown import path-bounded | `ValueError("roadmap_import_outside_repo")` for paths outside repo root |
| P0-5 | Markdown import never executes commands | No `subprocess` / shell during import |
| P0-6 | Import dry-run writes zero DB rows | `applied=False`, node/edge counts unchanged |
| P0-7 | Graph policy disabled by default | `roadmap_graph_enabled=0` for new/existing projects until explicit enable |
| P0-8 | Graph-enabled admission skips blocked backlog | Blocked roadmap nodes never promoted to tasks |
| P0-9 | Stale linked source → node `stale`, never `ready` | Missing/wrong-project source rows not admitted |
| P0-10 | No policy bypass | Routing/approval/review/merge gates unchanged by graph |

---

## P1 — High priority

| ID | Requirement | Test contract |
|---|---|---|
| P1-1 | Node upsert idempotent by `(project_id, node_type, ref_table, ref_id)` | Second upsert returns same id |
| P1-2 | `roadmap.next` / `/next` return only ready items | Blocked nodes excluded from payload |
| P1-3 | `select_next_best_work` orders priority desc, created_at asc | Deterministic ordering test |
| P1-4 | Unregistered project: stable error, no graph writes | CLI/RPC fail without mutation |
| P1-5 | Loop iteration records `roadmap_node_id` when graph selects work | Present in iteration caps/metadata |

---

## P2 — Documentation / smoke

| ID | Requirement |
|---|---|
| P2-1 | Docs state readiness ≠ correctness proof |
| P2-2 | Clean-wheel: `roadmap status` / `roadmap next` exit 0 without crash |
| P2-3 | `release check --json` remains `ok=true` on wheel install |

---

## Task 0 test file mapping

| File | Must cover |
|---|---|
| `test_roadmap_graph.py` | P0-1, P0-2, P1-1, migration 031 tables |
| `test_roadmap_readiness.py` | P0-3, P0-9, P1-2, P1-3, blocker until prerequisite done |
| `test_roadmap_import.py` | P0-4, P0-5, P0-6, apply writes nodes |
| `test_phase21_roadmap_e2e.py` | P1-2, P1-4, RPC/CLI/slash routing, P0-7, P0-8 |

---

## Commands for Gate verification

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_roadmap_graph \
  tests.test_roadmap_readiness \
  tests.test_roadmap_import \
  tests.test_phase21_roadmap_e2e -v
```