# Phase 5 Grok Implementation Handoff

Date: 2026-06-23
Integration branch: `external/coordinator-global-tui`
Plan: `docs/superpowers/plans/2026-06-23-commander-intelligence-integration.md` (r3)
Role: primary implementer (Grok)

## Objective

Connect Wave 4's TUI/Supervisor shell to the real Commander service. Fix wheel
migrations and XDG config loading (P0), add project-scoped goals (011), wire
`chat.send` through `supervisor_commander`, admit tasks via `create_task`, expose
`/goal` `/status` `/tasks` `/logs`, and verify PTY reconnect plus re-attach.

## Task Commits

| Task | Commit | Summary |
|------|--------|---------|
| 0 — P0 migrations | `3c87004` | Authoritative `src/local_cli_coordinator/migrations/`; mirror sync test; wheel isolation test |
| 1 — XDG config | `007e11b` | `load_config_from_dir()`; `config_runtime.load_config_for_paths()` |
| 2 — Goals 011 | `c32132a` | `011_project_goals.sql`; `active_goal_for_project()` |
| 3b — Registry | `df22bc0` | `get_project(conn, project_id)` |
| 3 — Admission | `f3b3684` | `admit_commander_response` → `create_task(..., commit=False)` |
| 4 — Commander chat | `a93e7d9` | `CommanderChatResult`; `handle_chat_send`; concurrency tests |
| 5 — Slash RPCs | `f6fe943` | `project.goal/status/tasks/logs`; TUI slash display |
| 6 — PTY smoke | `21fb43c` | Commander PTY output; chat replay; re-attach E2E |
| 7 — Docs | *(this commit)* | Operator docs + handoff |

Baseline plan doc: `d04e7d5`.

## Architecture Locked

- Chat roles: DB `assistant`, events/TUI `coordinator`
- `chat.send` only when goal **active** (draft/paused/blocked rejected)
- Migrations: authoritative package dir; root mirror must match
- Busy check before `Commander is thinking…` event
- `create_task(..., commit=False)` inside batch transactions
- No blind SQLite WAL assumptions in concurrency tests

## Codex Gate Evidence

### Gate A — P0 install (after Task 0)

```bash
PYTHONPATH=src python3 -m unittest tests.test_migration_mirror_sync -v
# Fresh venv — no PYTHONPATH=src:
python3 -m venv /tmp/coord-wheel-venv
python3 -m build
/tmp/coord-wheel-venv/bin/pip install --force-reinstall dist/local_cli_coordinator-*.whl
/tmp/coord-wheel-venv/bin/python3 -m unittest tests.test_wheel_migrations -v
```

### Gate B — schema + goals (after Task 2)

```bash
PYTHONPATH=src python3 -m unittest tests.test_goals tests.test_migration_mirror_sync -v
```

### Gate C — Commander chat (after Task 4)

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_supervisor_commander \
  tests.test_commander_chat_concurrency -v
```

### Gate D — slash commands (after Task 5)

```bash
PYTHONPATH=src python3 -m unittest tests.test_supervisor_methods -v
```

### Gate E — Phase 5 final (after Task 7)

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_migration_mirror_sync \
  tests.test_supervisor_commander \
  tests.test_supervisor_methods \
  tests.test_commander_chat_concurrency \
  tests.test_tui_pty.TuiPtyTests.test_pty_types_hello_and_sends_chat \
  tests.test_tui_pty.TuiPtyTests.test_reconnect_replays_chat_message_history \
  tests.test_global_tui_e2e.ReattachTuiE2ETests -v
git diff --check
```

Wheel install smoke (maintainers):

```bash
python3 -m build
python3 -m venv /tmp/coord-smoke-venv
/tmp/coord-smoke-venv/bin/pip install --force-reinstall dist/local_cli_coordinator-*.whl
/tmp/coord-smoke-venv/bin/coordinator supervisor status
```

## Real-Project Acceptance (manual)

Run from an installed wheel in a live repo (not `PYTHONPATH=src`):

```bash
cd /Users/xiafan/polymarket-crypto-threshold
coordinator supervisor status
coordinator                    # first launch — onboarding once
/goal 为项目添加 Coordinator 集成验收测试
/goal confirm
hi — 请生成 1 个小任务
/status
/tasks
# exit TUI (Ctrl+C or /quit)
coordinator                    # second launch — same project_id, no onboarding
/status
```

**Pass criteria:**

- Wheel install (no source tree on `PYTHONPATH`)
- Goal confirm activates project-scoped goal
- Chat admits or rejects with Commander reasons (not `Received:`)
- Re-attach skips onboarding; header shows same `project_id`
- `/status` and `/tasks` render in transcript

Record outcome in PR #1 before merge.

## Known Limitations

- Real Commander agent must be configured in global `agents.toml` for live chat
  (tests use fakes/mocks).
- `ui-tui` vitest may be unavailable in minimal dev envs; Python PTY/E2E tests
  are the Gate D/E UI evidence.
- PR #1 (`external/coordinator-global-tui`) should not merge until real install
  trial + Phase 5 acceptance complete.

## Delivery Format

Each task: focused commit, red→green test evidence, `git diff --check` clean.
Claude Code adversarial review between tasks per plan (operator-driven).