# Phase 5.5 Planning Kickoff

Date: 2026-06-26  
**Gate:** Gemini CONDITIONAL PASS (`2026-06-26-phase5-4-gemini-review-result.md`)  
**Prerequisite:** Phase 5.4 merged to `main` (PR #1)

## Scope lock — 5.5a (first sprint)

Implement before 5.5b:

| Wave | Deliverable |
|------|-------------|
| **A** | `chat.send` orchestration metadata + `project.task` detail v2 + TUI footer |
| **C** | `project.task.approve` / `retry` RPC (+ TUI slash); **defer `cancel`** to 5.5b |
| **D (partial)** | `supervisor.dashboard` counts only — no task titles |

**Deferred to 5.5b:** Wave B live log tail, Wave C cancel, Wave E rollback/cleanup.

## Design spec (Grok — before Claude red tests land on branch)

Create: `docs/superpowers/specs/2026-06-26-phase5-5-operational-ux-design.md`

Must resolve:

1. **Approve** — unblocks `awaiting_human` → `ready` only; never calls `commit_all`.
2. **Retry** — parity with `coordinator task retry` + project-scoped RPC errors.
3. **Dashboard** — `{project_id, goal_status, counts, active_workers}` only.
4. **Orchestration** — schema:
   ```json
   {
     "admitted": 1,
     "rejected": 0,
     "next_action": "string",
     "blocking_reasons": [],
     "rejection_reasons": []
   }
   ```
   Separate from `user_reply` (Phase 5.2 schema v2).

## Claude Task 0 — red tests (after spec freeze)

Files ready locally (do **not** commit to `main` before 5.4 merge):

| File | Tests |
|------|-------|
| `tests/test_phase5_5_chat_persona.py` | orchestration in json/rpc |
| `tests/test_phase5_5_task_detail.py` | `project.task` enrichment |
| `tests/test_phase5_5_task_control.py` | approve/retry/cancel |
| `tests/test_phase5_5_log_tail.py` | 5.5b — skip in 5.5a |
| `tests/test_phase5_5_dashboard.py` | dashboard RPC |

Commit on `phase5-5-operational-ux` branch only:

```bash
git checkout -b phase5-5-operational-ux main
git add tests/test_phase5_5_chat_persona.py tests/test_phase5_5_task_detail.py \
        tests/test_phase5_5_task_control.py tests/test_phase5_5_dashboard.py
git commit -m "test: capture Phase 5.5a operational UX requirements"
```

Expected: red tests fail until Grok Task 1–3.

## Verification commands (5.5a Gate A target)

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_phase5_5_chat_persona tests.test_phase5_5_task_detail \
  tests.test_phase5_5_task_control tests.test_phase5_5_dashboard -v

PYTHONWARNINGS=error::ResourceWarning PYTHONPATH=src python3 -m unittest discover -s tests -q
# Must return to 949+ passing (red tests green)
```

## Ownership

| Task | Owner |
|------|-------|
| Design spec | Grok |
| Red tests | Claude |
| Implementation | Grok |
| Docs | Claude |
| Gate A/E | Codex |

## Reference

- Plan: `docs/superpowers/plans/2026-06-26-phase5-5-operational-ux.md`
- Gemini review: `docs/superpowers/handoffs/2026-06-26-phase5-4-gemini-review-result.md`