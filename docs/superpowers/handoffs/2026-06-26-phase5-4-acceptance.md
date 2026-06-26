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

## Test Counts

| Suite | Tests | Status |
|-------|-------|--------|
| `test_cli_file_context.py` | 22 | ✅ all pass |
| `test_goal_sessions.py` | 50 | ✅ all pass |
| `test_execution_policy.py` | 42 | ✅ all pass |
| `test_phase5_4_e2e.py` | 13 | ✅ all pass |
| `test_cli_prompt.py` | 20 | ✅ all pass (regression) |
| Full suite | 932 | ✅ all pass (4 pre-existing fixture errors) |

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

- [x] Path traversal and redaction attacks (test_cli_file_context.py)
- [x] Resume/fork state and project isolation (test_goal_sessions.py)
- [x] Policy persistence and engine stage enforcement (test_execution_policy.py)
- [x] JSON/RPC headless output (test_phase5_4_e2e.py, test_cli_prompt.py)
- [x] Full regression green (932 passed)
- [x] Documentation updated (cli.md, troubleshooting.md)
