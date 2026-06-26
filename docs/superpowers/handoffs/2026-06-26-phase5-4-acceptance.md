# Phase 5.4 Acceptance Handoff

Date: 2026-06-26
Branch: `external/coordinator-global-tui`

## Commits

| Hash | Author | Description |
|------|--------|-------------|
| `072ca26` | Claude | Task 4 red tests: `tests/test_goal_sessions.py` |
| `b5f43bf` | Claude | Task 4 PTY selector and fork field boundary tests |
| `d3e2c33` | Claude | Task 7 red tests: `tests/test_execution_policy.py` |
| `a5ea7c6` | Grok | Task 8: persist restrictive execution policies |
| `ff4de36` | Grok | Task 9: enforce task execution stages and RPC mode |
| `e621fbe` | Claude | Task 10 E2E tests: `tests/test_phase5_4_e2e.py` |
| `feed21c` | Claude | Task 10 docs: `docs/cli.md`, `docs/troubleshooting.md` |
| `687949d` | Claude | Task 10 acceptance handoff (initial) |
| `9dc5f3e` | Grok | Task 11: integration fixture fix + gate record |
| `3f6e1ca` | Grok | Gate C repair: join background tick thread |
| `8307333` | Claude | Gate C repair: `LeakRegressionTests` (4 tests) |

## Gate C Sign-off

**Verdict: PASS** (Codex, 2026-06-26)

```bash
PYTHONWARNINGS=error::ResourceWarning PYTHONPATH=src python3 -m unittest \
  tests.test_phase2_gate.GateLiveEventTests.test_gate_socket_client_receives_live_event_after_subscribe -v
# OK

PYTHONWARNINGS=error::ResourceWarning PYTHONPATH=src python3 -m unittest \
  tests.test_phase5_4_e2e.LeakRegressionTests -v
# 4/4 OK

PYTHONWARNINGS=error::ResourceWarning PYTHONPATH=src python3 -m unittest discover -s tests -q
# 949/949 OK

git diff --check
# clean
```

**PR hygiene:** do not stage untracked local debug scripts (`run_attack_*.py`,
`debug_*.py`, `child*.py`, etc.).

## Test Counts

| Suite | Tests | Status |
|-------|-------|--------|
| `test_cli_file_context.py` | 25 | ✅ all pass |
| `test_goal_sessions.py` | 51 | ✅ all pass |
| `test_execution_policy.py` | 42 | ✅ all pass |
| `test_phase5_4_e2e.py` | 17 | ✅ all pass (incl. 4 leak regression) |
| `test_cli_prompt.py` | 21 | ✅ all pass (regression) |
| **Phase 5.4 focused total** | **156** | ✅ all pass |
| Full suite (`ResourceWarning=error`) | 949 | ✅ all pass |

## Task 11 Integration Gates (2026-06-26)

### TypeScript (`ui-tui`)

```text
npm run typecheck --prefix ui-tui  → PASS
npm run lint --prefix ui-tui      → PASS
npm test --prefix ui-tui -- --run → 14 files, 138 passed
```

### Python

```bash
git diff --check
# clean

PYTHONWARNINGS=error::ResourceWarning PYTHONPATH=src \
  python3 -m unittest discover -s tests -q
# Ran 945 tests — OK

PYTHONPATH=src python3 -m unittest \
  tests.test_cli_file_context tests.test_goal_sessions \
  tests.test_execution_policy tests.test_phase5_4_e2e tests.test_cli_prompt -v
# Ran 152 tests — OK

PYTHONPATH=src python3 -m unittest \
  tests.test_tui_bundle.WheelPackagingTest tests.test_wheel_migrations -v
# Ran 3 tests — OK
```

### Integration fix

`tests/fixtures/fake_commander.py` no longer emits `execution_policy` on Commander
task proposals (not part of schema v2). Without this fix,
`test_global_tui_e2e` failed with `tasks[0] has unknown fields: execution_policy`.

### Clean-wheel smoke (`/tmp/c54smoke`, installed wheel + `FakeSupervisor`)

| Command | Exit | Notes |
|---------|------|-------|
| `coordinator @README.md --mode json -p "summarize"` | 0 | JSON includes `context_files` + SHA-256 |
| `coordinator --resume --mode json` | 2 | Lists paused candidate (expected non-TTY exit 2) |
| `coordinator --fork 1 -p "docs only"` | 0 | Creates draft goal 2 |
| `coordinator --no-tools --print -p "explain status"` | 0 | Headless chat OK |
| `coordinator --tools read,grep --mode rpc -p "/status"` | 0 | Valid `ResponseEnvelope` |
| `coordinator --exclude-tools push,merge --print -p "make one small fix"` | 0 | Headless chat OK |

Smoke requires `COORDINATOR_HOME` in the `FakeSupervisor` host process so
`project.goals` / `project.goal.fork` RPC handlers can open the registry DB.

## Feature Coverage

### Wave A: File Context
- `@file` token parsing and canonicalization
- Repo-relative path resolution with symlink/escape blocking
- UTF-8 validation, size caps (128 KiB/file, 512 KiB aggregate, 16 files)
- `context_files` in `chat.send` params and JSON output
- SHA-256 hash verification
- Error codes: `context_missing`, `context_outside_repo`, `context_binary`, `context_too_large`

### Wave B: Goal Sessions
- `--resume` candidate listing (JSON/text/TTY modes)
- `--resume <id>` state transitions (paused→active, terminal→error)
- `--fork` draft creation with bounded progress context
- Project isolation and mutual exclusivity (`--continue`/`--resume`/`--fork`)
- `parent_goal_id` lineage tracking

### Wave C: Execution Policy and RPC
- `--tools`, `--no-tools`, `--exclude-tools` CLI parsing with alias canonicalization
- `ExecutionPolicy.compute_effective()` intersection and exclusion
- Admission gating (empty policy, read-only expected_files=0, no-test verification)
- Engine stage enforcement (no-edit, no-test, no-commit, no-push, no-merge)
- Policy persistence across DB close/reopen and daemon cycles
- `--mode rpc` producing valid `ResponseEnvelope` JSON
- `cli-local-` request_id prefix for local errors

## Known Limitations (Deferred)

- File glob expansion
- Directory attachments
- Binary/image attachments
- Editing config through TUI
- OS-level command sandboxing
- Cross-project goal cloning
- Remote clients or network RPC

## Gate Checklist

- [x] Path traversal and redaction attacks (`test_cli_file_context.py`)
- [x] Resume/fork state and project isolation (`test_goal_sessions.py`)
- [x] Policy persistence and engine stage enforcement (`test_execution_policy.py`)
- [x] JSON/RPC headless output (`test_phase5_4_e2e.py`, `test_cli_prompt.py`)
- [x] Full regression green (949 passed)
- [x] Wheel packaging (`WheelPackagingTest`, `test_wheel_migrations`)
- [x] Clean-wheel smoke (six CLI commands above)
- [x] Documentation updated (`docs/cli.md`, `docs/troubleshooting.md`)
- [x] Full-suite ResourceWarning leak fixed (`test_phase2_gate` live-event thread join)
- [x] Codex Gate C / final independent sign-off (PASS)