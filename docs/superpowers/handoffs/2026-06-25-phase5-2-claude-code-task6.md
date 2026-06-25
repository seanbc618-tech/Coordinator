# Claude Code Handoff: Phase 5.2 Task 6 Gates and Real Smoke

Your scope is verification only. Do not modify production architecture unless a
gate exposes a reproducible product bug — report those to Grok with exact output.

Repository: `/Users/xiafan/Coordinator`
Branch: `external/coordinator-global-tui`
Baseline: `0401823` (Task 5 complete)
Plan: `docs/superpowers/plans/2026-06-25-phase5-2-conversation-runtime.md`

## Prerequisites

Grok confirmed `src/local_cli_coordinator/tui_bundle/` matches `ui-tui/dist/`
(`build_hash: fa5e760bfe0d0573`). Rebuild only if you change `ui-tui/src/`:

```bash
npm run build --prefix ui-tui
```

Install the **workspace** Coordinator before real smoke. The default
`~/.local/bin/coordinator` may be an older build without `supervisor restart`:

```bash
cd /Users/xiafan/Coordinator
python3 -m pip install -e .
which coordinator   # confirm it resolves to the editable install
coordinator supervisor --help   # must list: start, status, stop, restart
```

If PATH still points at an old binary, use the pip-reported script path explicitly.

## Gate 1 — TypeScript

```bash
cd /Users/xiafan/Coordinator
npm run typecheck --prefix ui-tui
npm run lint --prefix ui-tui
npm test --prefix ui-tui -- --run
npm run build --prefix ui-tui
```

Record pass/fail counts. After `npm run build`, confirm bundle hash unchanged unless
source changed:

```bash
git diff -- src/local_cli_coordinator/tui_bundle/
```

## Gate 2 — Full Python Suite (isolated XDG)

```bash
rm -rf /private/tmp/coordinator-phase52
XDG_CONFIG_HOME=/private/tmp/coordinator-phase52/config \
XDG_DATA_HOME=/private/tmp/coordinator-phase52/data \
XDG_STATE_HOME=/private/tmp/coordinator-phase52/state \
PYTHONWARNINGS=error::ResourceWarning \
PYTHONPATH=src python3 -m unittest discover -s tests -q
```

## Gate 3 — Focused Phase 5.2 Regression Subset

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_supervisor_process \
  tests.test_supervisor_cli \
  tests.test_commander_protocol \
  tests.test_commander_runner \
  tests.test_commander_chat \
  tests.test_supervisor_commander \
  tests.test_tui_pty \
  tests.test_tui_bundle \
  tests.test_global_tui_e2e \
  -v
```

## Gate 4 — Wheel Packaging

```bash
PYTHONPATH=src python3 -m unittest tests.test_tui_bundle.WheelPackagingTest -v
```

## Gate 5 — Whitespace

```bash
git diff --check
```

Must produce no output on a clean tree.

## Gate 6 — Real TUI Smoke (polymarket)

Repo: `/Users/xiafan/polymarket-crypto-threshold`

### 6a. Legacy cleanup (if needed)

If an old Supervisor is still serving, restart cleanly:

```bash
cd /Users/xiafan/polymarket-crypto-threshold
coordinator supervisor status
coordinator supervisor restart
coordinator supervisor status
```

Confirm exactly one running PID and that `restart` changes the PID when a server
was already active.

### 6b. Interactive TUI script

Launch `coordinator` (no args) from the git repo root. Enter these inputs in order
and record visible transcript behavior:

```text
/help
你好
？？？
如何启动？
创建一个只读任务，运行 uv run ruff check src/ tests/ 并报告结果。
/tasks
/task <new-id>
/quit
```

### 6c. Pass criteria

| Check | Expected |
|-------|----------|
| `/help` | Local help text; no `chat.send` |
| `你好`, `？？？`, `如何启动？` | Natural-language replies; **zero new tasks** |
| Explicit ruff task request | May admit a read-only/report task after policy checks |
| `/tasks`, `/task <id>` | Deterministic RPC handling |
| Unknown slash (optional spot-check `/taskz`) | `Unknown command: /taskz. Use /help.` — stays local |
| Visible Commander text | Uses `user_reply` tone; no raw `duplicate title`, `admission`, or `linked task` diagnostics in main transcript |
| Runtime trust | If incompatible Supervisor message appears, `coordinator supervisor restart` recovers |

## Deliverables

1. Append exact command output to
   `docs/superpowers/handoffs/2026-06-25-phase5-2-acceptance.md` under a new
   **Task 6 Gate Results** section with date and commit hash tested.
2. If all gates pass, commit:

```text
docs: record Phase 5.2 Task 6 gate results
```

3. If a gate fails due to product behavior, **do not** loosen assertions or patch
   production code. File a short blocker note in the acceptance doc and stop.

## Do Not Edit

- `src/local_cli_coordinator/supervisor_process.py` lifecycle fixes — Grok
- Commander schema/admission policy — Grok
- TUI state architecture — Grok

Allowed: docs updates, acceptance output collection, test reruns only.