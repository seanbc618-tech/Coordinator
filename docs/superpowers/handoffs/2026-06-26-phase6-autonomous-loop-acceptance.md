# Phase 6 Autonomous Loop Core — Acceptance Handoff

Date: 2026-06-26
Branch: `main`
Plan: `docs/superpowers/plans/2026-06-26-phase6-autonomous-loop-core.md`

## Status

**Task 0 (Claude):** Red tests committed — 30/30 red (all `ModuleNotFoundError`)
**Task 7 (Claude):** Docs + handoff committed

## Red Test Suites

| File | Tests | Status |
|------|-------|--------|
| `test_autonomous_backlog.py` | 8 | 🔴 8 red |
| `test_task_evaluator.py` | 8 | 🔴 8 red |
| `test_loop_autonomy.py` | 9 | 🔴 9 red |
| `test_phase6_autonomous_loop_e2e.py` | 5 | 🔴 5 red |
| **Total** | **30** | **30 red** |

## Required Red Test Names (from plan)

| Test name | File | Captures |
|-----------|------|----------|
| `test_backlog_dedupes_duplicate_open_items` | `test_autonomous_backlog.py` | Duplicate rejection |
| `test_backlog_promote_creates_project_task` | `test_autonomous_backlog.py` | Backlog → task |
| `test_evaluator_records_terminal_task_once` | `test_task_evaluator.py` | Idempotent evaluation |
| `test_evaluator_flags_failed_task_as_followup` | `test_task_evaluator.py` | Fail verdict |
| `test_loop_waits_when_project_has_running_task` | `test_loop_autonomy.py` | Serial policy |
| `test_loop_admits_one_backlog_item_when_idle` | `test_loop_autonomy.py` | Idle admission |
| `test_loop_records_every_iteration_reason` | `test_loop_autonomy.py` | Persisted decisions |
| `test_project_loop_status_is_project_scoped` | `test_phase6_autonomous_loop_e2e.py` | Project isolation |

## Docs

| File | Content |
|------|---------|
| `docs/cli.md` | Updated banner + `/loop`, `/backlog`, `/evals`, `/loop step` sections |
| `docs/autonomous-loop.md` | Full loop documentation: config, backlog, evaluation, failure modes |

## Task Ownership

| Task | Owner | Status |
|------|-------|--------|
| 0: Red tests + fixtures | Claude | ✅ done |
| 1: Migration 014 + data access | Grok | pending |
| 2: Backlog governance | Grok | pending |
| 3: Rule-based evaluator | Grok | pending |
| 4: Autonomous iteration engine | Grok | pending |
| 5: Supervisor integration | Grok | pending |
| 6: RPC + slash commands | Grok | pending |
| 7: Docs + handoff + smoke | Claude | ✅ done |
| 8: Gemini adversarial review | Gemini | pending |
| 9: Final Gate E | Codex | pending |

## Gate Sequence

```
Task 0 (Claude red tests) ← DONE
  → Task 1 (Grok migration) → Gate A (Codex)
  → Task 2 (Grok backlog) → Gemini review
  → Task 3 (Grok evaluator) → Gate B (Codex)
  → Task 4 (Grok loop engine) → Gate C (Codex)
  → Task 5 (Grok supervisor) → Gate D (Codex)
  → Task 6 (Grok RPC)
  → Task 7 (Claude docs) ← DONE
  → Task 8 (Gemini adversarial)
  → Task 9 (Codex Gate E final)
```

## Verification

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_autonomous_backlog \
  tests.test_task_evaluator \
  tests.test_loop_autonomy \
  tests.test_phase6_autonomous_loop_e2e -v
# Expected: 30 tests, all fail with ModuleNotFoundError
```
