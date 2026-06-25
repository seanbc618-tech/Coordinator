# Phase 5.2 Conversation and Runtime Trust — Acceptance Handoff

Date: 2026-06-25
Plan: `docs/superpowers/plans/2026-06-25-phase5-2-conversation-runtime.md`
Branch: `external/coordinator-global-tui`
Baseline: Grok Tasks 1–4 at `c54cdcb`, schema v2 fixtures at `e1ae77b`

## Objective

Complete Phase 5.2 Task 5 (deterministic fixtures, PTY coverage, documentation)
after Grok delivered runtime identity, Commander schema v2, operator-language task
outcomes, and line-aware transcript routing.

## Delivered Changes

| Area | Summary |
|------|---------|
| `tests/fixtures/fake_supervisor.py` | Schema v2 `chat.send` events, runtime identity ping, session reset between PTY tests |
| `tests/fixtures/fake_commander.py` | Schema v2 with `intent` and `user_reply` |
| Commander policy tests | Updated fixtures to schema v2 across adversarial, e2e, failures, replenishment |
| `tests/test_tui_pty.py` | Phase 5.2 layout gates at 40/80/120 cols, unknown slash, chat-once, normalized frame assertions |
| `docs/tui.md` | Trusted runtime restart, chat vs slash vs explicit task requests |
| `docs/troubleshooting.md` | Incompatible Supervisor recovery steps |

## Verification Commands

```bash
npm run typecheck --prefix ui-tui
npm test --prefix ui-tui -- --run
PYTHONPATH=src python3 -m unittest \
  tests.test_tui_pty \
  tests.test_commander_adversarial \
  tests.test_commander_chat_concurrency \
  tests.test_commander_e2e \
  tests.test_commander_failures \
  tests.test_commander_replenishment \
  tests.test_supervisor_methods \
  -q
```

### Recorded Output (2026-06-25)

TypeScript:

```text
Test Files  14 passed (14)
     Tests  138 passed (138)
```

Focused Python gate (75 tests):

```text
Ran 75 tests in 235.211s
OK
```

Full Python suite (isolated XDG):

```bash
XDG_CONFIG_HOME=/private/tmp/coordinator-phase52/config \
XDG_DATA_HOME=/private/tmp/coordinator-phase52/data \
XDG_STATE_HOME=/private/tmp/coordinator-phase52/state \
PYTHONWARNINGS=error::ResourceWarning \
PYTHONPATH=src python3 -m unittest discover -s tests -q
```

```text
Ran 792 tests in 355.871s
OK
```

Whitespace check:

```bash
git diff --check
```

(no output — clean)

## Manual Smoke (Task 6 — Grok)

```bash
cd /Users/xiafan/polymarket-crypto-threshold
coordinator supervisor restart
coordinator supervisor status
coordinator
/help
你好
？？？
如何启动？
创建一个只读任务，运行 uv run ruff check src/ tests/ 并报告结果。
/tasks
/task <new-id>
/quit
```

Verify: one serving Supervisor PID, greetings create zero tasks, explicit task
request may admit work, unknown slash stays local, incompatible-runtime message
recovers via `coordinator supervisor restart`.

## Known Limitations

- PTY layout assertions normalize wrapped lines because Ink wraps coordinator
  replies across terminal columns.
- `FakeSupervisor.reset_session()` clears replay history between PTY tests so
  reconnect dedup assertions are not polluted by prior cases in the shared server.