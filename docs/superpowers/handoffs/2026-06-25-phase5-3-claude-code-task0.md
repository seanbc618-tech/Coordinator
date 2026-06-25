# Claude Code Handoff: Phase 5.3 Task 0 Red Tests

Your scope is **tests only**. Do not modify production code under `src/`.

Repository: `/Users/xiafan/Coordinator`
Branch: `external/coordinator-global-tui`
Baseline: `e8e9bf9`
Plan: `docs/superpowers/plans/2026-06-25-phase5-3-pi-inspired-cli-ux.md` (Task 0)
Design: `docs/superpowers/specs/2026-06-25-phase5-3-pi-inspired-cli-ux-design.md`

## Goal

Add **failing** regression tests that describe Phase 5.3 CLI behavior before Grok
implements it. Tests must fail for the **right reason** today (missing flags /
missing `config` subcommand / no Supervisor `chat.send` path), not because
assertions are loose or skipped.

After this task: **stop**. Send Grok the commit hash. Do not implement features
to make tests pass.

---

## Files

| Action | Path |
|--------|------|
| **Create** | `tests/test_cli_prompt.py` |
| Optional | `tests/test_tui_launcher.py` — only if you extract a shared helper already used elsewhere |

Do **not** edit:

- `src/local_cli_coordinator/cli.py` (Grok Task 1+)
- `src/local_cli_coordinator/cli_chat.py` (does not exist yet — expected)
- Commander / Supervisor production modules
- `tests/test_supervisor_process.py`, `tests/test_supervisor_cli.py` (Grok)

---

## Test Infrastructure Patterns

Reuse existing helpers:

```python
from tests.helpers import ROOT, SRC, run_cli, init_git_repo
```

For isolated global runtime (Supervisor + project registry), follow
`tests/test_supervisor_cli.py`:

```python
def _run_cli_with_home(home: Path, *args: str, cwd: Path | None = None):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC)
    env["COORDINATOR_HOME"] = str(home)
    return subprocess.run(
        [sys.executable, "-m", "local_cli_coordinator", *args],
        cwd=cwd or ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
```

For Supervisor RPC assertions, reuse `tests/fixtures/fake_supervisor.py`:

- Start `FakeSupervisor` on a temp socket under `home`
- Point `COORDINATOR_HOME` at the same `home` so `resolve_runtime_paths()` finds the socket
- Use `server.drain_requests()` to assert `chat.send` was (or was not) called

Register a git repo as a project before prompt tests (see
`tests/test_global_tui_e2e.py` or `register_project` + `inspect_project`).

Mock TUI launch to prove print mode does not open Ink:

```python
with mock.patch("local_cli_coordinator.tui_launcher.launch_tui") as launch_mock:
    ...
launch_mock.assert_not_called()
```

---

## Required Test Cases

Create one test class per area. Suggested names (you may refine):

### 1. `CliPromptParserRedTests` — flags not implemented yet

These should **fail today** with `SystemExit(2)` or argparse error on stderr.

| Test | Invocation | Assert today | Assert after Grok Task 1 |
|------|------------|--------------|--------------------------|
| `test_print_prompt_flag_parses` | `["-p", "你好", "--print"]` | exit != 0 or unknown arg | exit 0 path exists |
| `test_mode_json_flag_parses` | `["--mode", "json", "-p", "hello", "--print"]` | same | same |
| `test_continue_flag_parses` | `["--continue", "-p", "next", "--print"]` | same | same |
| `test_positional_prompt_parses` | `["检查项目状态", "--print"]` | same | same |
| `test_print_implies_no_tui` | document via comment; full check in test 2 | N/A | `--print` sets no-tui |

Also add **one positive guard** that must pass today:

| Test | Assert |
|------|--------|
| `test_existing_supervisor_subcommand_unaffected` | `main(["supervisor", "status"])` still works (mock socket missing → exit 1, not argparse error) |

Reference: `tests/test_cli.py`, `tests/test_supervisor_cli.py`

### 2. `CliPromptPrintRedTests` — headless chat.send path

Setup per test:

1. `tempfile.TemporaryDirectory()` → `home`
2. `init_git_repo(repo_dir)` under `home`
3. Register project + create active goal with commander fixture (conversation
   fixture from `test_supervisor_commander._write_conversation_fixture`)
4. Start `FakeSupervisor` on `home/coordinator.sock` (or path from
   `resolve_runtime_paths()` after `COORDINATOR_HOME` set)
5. Run CLI from `repo_dir`:

```bash
coordinator --print -p "你好"
```

| Assert | Why |
|--------|-----|
| `returncode == 0` (future) | **Fails today** — flags unknown |
| `chat.send` in `server.drain_requests()` | **Fails today** — no RPC |
| stdout contains conversation `user_reply` (e.g. 你好) | **Fails today** |
| stdout does **not** contain admission leak tokens (`duplicate title`, `linked task`, `admission`, `no duplicate`) | inherit list from `test_supervisor_commander.py` |
| `launch_tui` **not** called | **Fails today** |

### 3. `CliPromptJsonRedTests` — JSON envelope

Same setup as test 2. Run:

```bash
coordinator --print --mode json -p "现在有什么任务？"
```

Parse stdout as JSON and assert keys exist (design spec minimum):

```python
REQUIRED_JSON_KEYS = {
    "ok", "project_id", "goal_id", "user_reply", "intent",
    "admitted", "rejected", "accepted_task_ids", "error",
}
```

| Assert today | Assert after Grok Task 3 |
|--------------|--------------------------|
| exit != 0 or stdout not valid JSON | valid JSON, all keys present, `ok is True` |

### 4. `CliPromptProjectRedTests` — unknown / unregistered repo

Run from a non-git directory or git repo **not** registered in global DB:

```bash
coordinator --print -p "hello"
```

| Assert |
|--------|
| `returncode != 0` (after implementation) — **may already fail today** with argparse error; update assertion message to distinguish argparse vs "project not registered" once Grok implements |

Document expected error substring after implementation: `not registered` or
`not a git repository` (match `tui_launcher` errors).

### 5. `CliContinueRedTests` — `--continue` goal binding

Setup: project with **two** goals — one `completed`, one `active` (or `paused`).
Run:

```bash
coordinator --continue --print -p "下一步"
```

| Assert after implementation |
|----------------------------|
| `chat.send` params reference the **latest non-terminal** goal id |
| fails with clear error when only terminal goals exist |

**Fails today** because `--continue` is unknown.

### 6. `CliConfigRedTests` — read-only config command

```bash
coordinator config
```

| Assert today | Assert after Grok Task 5 |
|--------------|------------------------|
| exit 2 (unknown subcommand) | exit 0 |
| | stdout contains sections: `agents`, `repos` (or `allowlist`), `runtime`, `paths` (or `XDG`) |

Use isolated `COORDINATOR_HOME` with a minimal valid `config.toml` so output is
deterministic.

### 7. `CliLegacyChatRegressionTests` — do not break old path

Call existing REPL entry without new flags:

```python
from local_cli_coordinator.cli import main
# mock input/send_chat_message as in existing patterns
```

| Assert |
|--------|
| `coordinator chat` code path still reachable |
| No change required to pass today — this test should **pass now** and stay green |

---

## Run Commands

Run **only** the new module first:

```bash
cd /Users/xiafan/Coordinator
PYTHONPATH=src python3 -m unittest tests.test_cli_prompt -v
```

Record output. Expected today:

- Most new tests **ERROR** or **FAIL**
- `test_existing_supervisor_subcommand_unaffected` and legacy chat regression **PASS**

Then confirm you did not break a small smoke subset:

```bash
PYTHONPATH=src python3 -m unittest tests.test_cli tests.test_supervisor_cli.SupervisorParserTest -v
```

---

## Commit

Single commit only:

```text
test: capture Phase 5.3 CLI prompt regressions
```

Commit body (optional) should list:

- number of new tests
- which fail today and why (e.g. "7 FAIL: argparse unknown --print")
- hash sent to Grok

---

## Deliverable Back to Grok

Post:

```text
Phase 5.3 Task 0 done.
Commit: <hash>
New tests: N total, M failing (expected), K passing (regression guards)
Failure sample: tests.test_cli_prompt.CliPromptPrintRedTests.test_print_sends_chat_rpc
```

---

## Common Mistakes to Avoid

1. **Do not** skip tests with `@unittest.skip` — they must fail visibly.
2. **Do not** weaken assertions to get green (e.g. `assertIn("error", stderr)`).
3. **Do not** implement `cli_chat.py` or edit `cli.py` to parse flags.
4. **Do not** run the full 792-test suite unless the small smoke above fails
   unexpectedly.
5. If a test accidentally passes for the wrong reason, tighten it before commit.

---

## Reference Docs

- Phase 5.2 conversation rules: `docs/superpowers/specs/2026-06-25-phase5-2-conversation-runtime-design.md`
- JSON envelope: design spec § "JSON envelope (public CLI schema)"
- Prior Claude handoff style: `docs/superpowers/handoffs/2026-06-25-phase5-2-claude-code-support.md`