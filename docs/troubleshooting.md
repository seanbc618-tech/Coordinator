# Troubleshooting Coordinator

Common problems when installing, launching, or operating the global Coordinator
TUI.

## `coordinator`: not a git repository

**Symptom:** `error: not a git repository: /path/to/cwd` (exit code 2)

**Cause:** The no-argument launcher requires a Git working tree.

**Fix:** `cd` into your project repository (or a subdirectory) and run
`coordinator` again. Use administrative subcommands from anywhere:
`coordinator supervisor status`, `coordinator migrate`, etc.

## `error: node executable not found in PATH`

**Symptom:** Exit code 1 before the TUI opens.

**Cause:** Node.js is not installed or not on `PATH`.

**Fix:** Install Node.js 18+ and verify:

```bash
which node
node --version
```

Then retry `coordinator`.

## `error: Supervisor failed to become ready`

**Symptom:** Launcher or first TUI connect fails after ~30 seconds.

**Causes:**

- Stale socket or lock from a crashed Supervisor
- Port/socket permissions on `~/.local/state/coordinator/`
- Supervisor crash on startup

**Fix:**

```bash
coordinator supervisor status
tail -50 ~/.local/state/coordinator/supervisor.log
```

If status reports not running but `coordinator.sock` exists, remove stale files
only when no Coordinator process is active:

```bash
coordinator supervisor stop   # if reachable
rm -f ~/.local/state/coordinator/coordinator.sock
rm -f ~/.local/state/coordinator/supervisor.lock
```

Then run `coordinator` again (it starts a fresh detached Supervisor).

## Supervisor is incompatible with this Coordinator install

**Symptom:** TUI or `coordinator` exits immediately with:

```text
Supervisor is incompatible with this Coordinator install.
Run: coordinator supervisor restart
```

**Cause:** A stale Supervisor from an older install is still serving on the Unix
socket. It responds to `system.ping` but lacks the runtime identity and
capabilities expected by the current Coordinator package.

**Fix:**

```bash
coordinator supervisor restart
coordinator supervisor status
```

`restart` shuts down the old process, waits for the socket and lock to disappear,
then starts exactly one new Supervisor. Verify the PID changed and only one
`coordinator supervisor status` reports running.

If `restart` times out, check for orphaned processes manually, remove stale
`coordinator.sock` / `supervisor.lock` only when no Coordinator process is active,
then run `coordinator supervisor restart` again.

## TUI bundle missing or corrupt

**Symptom:**

```
error: TUI bundle hash mismatch … Rebuild and reinstall with:
npm run build --prefix ui-tui && pip install --force-reinstall .
```

**Cause:** Wheel installed without a built bundle, or manual edits to packaged
files.

**Fix** (from the Coordinator source tree):

```bash
npm run build --prefix ui-tui
pip install --force-reinstall .
```

For end users without a checkout, reinstall from a freshly built wheel:

```bash
python3 -m build
pip install --force-reinstall dist/local_cli_coordinator-*.whl
```

## `coordinator supervisor status` says not running

**Symptom:** `Supervisor is not running` (exit code 1)

**Fix:** Launch the TUI (`coordinator` from a repo) — it calls `ensure_supervisor`
automatically. Or start manually:

```bash
coordinator supervisor start --foreground
```

Check logs at `~/.local/state/coordinator/supervisor.log`.

## TUI shows `offline` or `reconnecting`

**Symptom:** Header stuck on yellow/red connection state.

**Causes:**

- Supervisor stopped or crashed
- Socket file removed while TUI is open
- Permission change on state directory

**Fix:**

1. `coordinator supervisor status`
2. If not running, run `coordinator` from any repo to restart
3. Detach (`/quit` or Ctrl+C) and reconnect

Events replay from your last cursor after reconnect.

## Migration declined or refused

**Symptom:** First launch exits after printing migration summary.

**Causes:**

- You answered `N` at the prompt
- Non-interactive session (CI, piped stdin) without `--yes`

**Fix:**

```bash
coordinator migrate --source /path/to/legacy --dry-run   # validate
coordinator migrate --source /path/to/legacy --yes       # migrate
```

Set `COORDINATOR_LEGACY_ROOT` if auto-detection misses your old install:

```bash
export COORDINATOR_LEGACY_ROOT=/path/to/legacy
coordinator
```

## Migration failed / corrupt journal

**Symptom:** `migration failed: …` or `Migration journal is corrupt`

**Fix:**

1. Global state was rolled back automatically if promotion failed
2. Delete corrupt journal: `rm ~/.local/share/.migration-journal.json`
3. Restore from backup if needed (see [migration.md](migration.md))
4. Retry dry run, then full migration

The legacy source directory is never deleted — your original data remains safe.

## Onboarding keeps reappearing

**Symptom:** Registration screen on every launch.

**Causes:**

- Esc was pressed previously (project never registered)
- Repository path changed without reconfirmation
- `COORDINATOR_HOME` points at a fresh data directory

**Fix:** Press **Enter** on the onboarding screen to register. If the repo moved,
confirm the new canonical path shown on the warning line.

## Tasks stuck in `awaiting_human`

**Symptom:** `/status` shows tasks in `awaiting_human`; workers idle.

**Cause:** Independent review failed or fallback agents were both blocked.

**Fix:** Read the review packet:

```bash
ls tasks/review/
coordinator task show <task_id>
```

Approve, reject, or retry from the administrative CLI. This is expected behavior
for risky changes — not a TUI defect.

## `/stop` or `/shutdown` does nothing

**Symptom:** Destructive command ignored.

**Cause:** Destructive commands need **double confirmation**.

**Fix:** Type `/stop` or `/shutdown` twice in a row. Coordinator prompts after
the first attempt.

## Detach hangs or terminal is garbled

**Symptom:** Ctrl+C or `/quit` leaves raw mode or mouse reporting enabled.

**Cause:** PTY buffer not drained (rare on some terminals).

**Fix:**

```bash
reset
```

Detach is designed to restore canonical mode; if it fails, `reset` clears escape
sequences. Report persistent cases with your terminal emulator and OS version.

## `pip install` works but `coordinator` not found

**Symptom:** Command not found after wheel install.

**Fix:** Ensure the venv is activated or `~/.local/bin` is on `PATH`:

```bash
python3 -m pip show local-cli-coordinator
which coordinator
```

Reinstall into the environment you use daily:

```bash
pip install --force-reinstall dist/local_cli_coordinator-*.whl
```

## `unsupported method 'system.help'`

**Symptom:** `/help` prints an RPC error mentioning `system.help`.

**Cause:** Stale TUI bundle or pre-Phase 5.1 build where `/help` called the
Supervisor.

**Fix:** Rebuild and reinstall (`npm run build --prefix ui-tui && pip install
--force-reinstall .`). Phase 5.1 generates `/help` locally from the command
catalog.

## User message appears twice

**Symptom:** Your chat text shows up two times in the transcript.

**Cause:** Optimistic local echo plus Supervisor `chat.message` replay (fixed in
Phase 5.1).

**Fix:** Reinstall the current TUI bundle. After the fix, only the server event
renders your message (`> your text`).

## Task failed with `no changed files`

**Symptom:** `/task <id>` or activity shows `no changed files`.

**Meaning:** A **code-edit** task finished without a worktree patch. Inspect the
agent log via `/task <id>`.

**Report-only exception:** Baseline/acceptance tasks tagged as report-only (tests
capability, goal mentions reporting without code changes) run verification and
can finish `done` with no changed files.

## Agent cannot read prompt file

**Symptom:** Agent log says it needs permission to read the prompt outside the
worktree.

**Fix (Phase 5.1+):** Prompts are copied under
`<worktree>/.coordinator/<task-id>/prompt.md`. Re-run the task after upgrading;
`/task <id>` should show `worktree_prompt` in artifacts.

## Chat rejected: goal is draft

**Symptom:** Sending a message prints an error like
`Goal is draft. Run /goal confirm before chatting.`

**Cause:** Phase 5 chat requires an **active** goal. `/goal <objective>` creates
a draft preview only; Commander does not run until you confirm.

**Fix:**

```
/goal <your objective>    # create draft + preview proposals
/goal confirm             # activate goal
```

Then send plain-language chat again. Use `/goal` alone to check status, or
`/status` for task counts and goal summary.

## Chat rejected: no active goal

**Symptom:** `No active goal. Use /goal <objective> then /goal confirm.`

**Fix:** Create and confirm a goal as above. Each project has its own goal scope
after migration 011.

## Commander is already running

**Symptom:** `Commander is already running; try again after the current run finishes.`

**Cause:** Only one Commander invocation runs per active goal at a time. A
previous `chat.send` or replenishment is still in flight.

**Fix:** Wait for `Commander is thinking…` to clear and for the coordinator
reply (or error) to appear. `/status` remains available while chat is blocked.
Do not send duplicate chat messages until the run completes.

## Commander timed out (120 seconds)

**Symptom:** Chat fails after ~120 seconds; transcript or RPC error mentions
timeout; goal may move to `blocked` depending on policy.

**Cause:** `COMMANDER_TIMEOUT_SECONDS` is 120. Slow or hung Commander agent
processes exceed this limit.

**Fix:**

1. Check `~/.local/state/coordinator/supervisor.log` and Commander run logs
2. Verify the Commander agent command in `agents.toml` starts reliably
3. Retry with a shorter, clearer chat message after `/status` shows goal `active`
4. If the goal is `blocked`, resolve the blocker (`coordinator goal status` or
   `/goal`) before chatting again

## Permission errors on global directories

**Symptom:** Cannot create socket, database, or config files.

**Fix:**

```bash
ls -la ~/.config/coordinator ~/.local/share/coordinator ~/.local/state/coordinator
```

Coordinator creates directories with mode `0700`. Fix ownership if another user
created them:

```bash
chown -R "$USER" ~/.config/coordinator ~/.local/share/coordinator ~/.local/state/coordinator
```

## tool_unknown / tool_conflict

```
error: unknown tool name 'deploy'
error: --tools and --no-tools are mutually exclusive
```

Check the tool vocabulary: `read`, `search`, `test`, `edit`, `commit`, `push`,
`merge`. Aliases `grep` → `search` and `write` → `edit` are accepted.

`--tools` and `--no-tools` cannot be combined. `--tools` and `--exclude-tools`
may be combined; exclusion takes precedence.

## tool_policy_rejected

```
all task proposals rejected by execution policy
```

The effective execution policy (client flags ∩ server repo policy) forbids all
stages needed by the proposed task. Common causes:

- `--no-tools` was used (admits zero tasks).
- `--tools read,search` but the proposal expects `expected_files > 0`.
- The proposal has `verification_commands` but `test` is not in the allowed set.

Check with `--mode json` to see the `execution_policy` and `rejected` counts.

## execution_policy forbids edit

```
execution policy forbids edit: worktree has changes
```

The engine detected worktree changes but the effective policy excludes `edit`.
This means the Commander proposed a write task under a read-only policy.

If you need to edit, re-run without `--exclude-tools edit` or with
`--tools` that includes `edit`.

## Context file errors

```
error: context file not found: docs/missing.md
error: context file outside repository: /etc/passwd
error: context file is binary: image.png
error: context files exceed 512 KiB aggregate limit
```

Context files must be repo-relative, UTF-8, ≤ 128 KiB each, and the aggregate
must be ≤ 512 KiB / 16 files. Symlink traversal and `..` escape are blocked.

## RPC mode output

```
{"protocol_version":1,"request_id":"cli-local-abc123","ok":false,"error":"..."}
```

RPC mode always emits exactly one `ResponseEnvelope` JSON line. If you see
multiple lines or parse errors, the CLI encountered an unhandled exception —
check stderr.

For local validation errors (unknown slash command, missing project), the
`request_id` is prefixed `cli-local-`. Remote errors from the Supervisor use
the original `request_id`.

## `coordinator init`: not a git repository

**Symptom:** `error: not a git repository` or JSON `errors[].code` =
`invalid_project` (exit code 1).

**Cause:** `coordinator init` requires a Git working tree at the current
directory or `--path`.

**Fix:** `cd` into your repository root (or pass `--path /path/to/repo`). Use
`coordinator init --dry-run --json` first to preview planned config changes
without writing files.

## `coordinator init --dry-run` wrote files

**Symptom:** Config files appeared after a dry-run.

**Cause:** This is a bug — dry-run must have zero filesystem side effects.

**Fix:** Remove unintended files under `COORDINATOR_HOME` or XDG config, then
re-run with `--dry-run --json` and confirm `data.would_write` lists paths but
nothing is created on disk. Report if files are written.

## Admin `--json` parse errors

**Symptom:** `jq` or scripts fail to parse stdout; or `ok: false` with typed
`errors[].code`.

**Common codes:**

| Code | Command | Fix |
|---|---|---|
| `supervisor_not_running` | `supervisor status --json` | `coordinator supervisor start` or launch TUI |
| `missing_config` | `config --json` | Run `coordinator init` or copy templates from install docs |
| `invalid_project` | `loop --json`, slash JSON | `cd` into a registered repo or `coordinator project add` |
| `confirmation_required` | `init` without `--yes` | Re-run with `--yes` after reviewing dry-run output |

JSON admin output uses the Phase 6D envelope (`schema_version`, `data`,
`warnings`, `errors`). Do not parse human text from `--json` commands.

## `mock-provider` fixture errors

**Symptom:** Exit code 2 with `fixture schema`, `prompt not found`, or
`fixture role mismatch`.

**Cause:** The fixture path is wrong, the JSON does not match the expected
Commander/worker schema, or the Commander prompt file is missing.

**Fix:**

```bash
coordinator mock-provider run commander \
  --fixture "$(pwd)/tests/fixtures/commander/one-task.json" \
  --prompt /path/to/prompt.md
```

Use absolute fixture paths in clean-wheel smoke. Ensure agent `command` templates
include `mock-provider` when routing through the harness.

## Worker-state or event v2 tables missing

**Symptom:** Supervisor logs mention `no such table: worker_state_snapshots` or
`supervisor_events_v2`.

**Cause:** Database predates migration 016.

**Fix:**

```bash
coordinator migrate
coordinator doctor --json
```

Both migration roots ship migration 016 in the installed wheel.

## Strategic autonomy tables missing (Phase 7)

**Symptom:** `/strategy`, `/recoveries`, or `/agents` return RPC errors mentioning
`project_milestones`, `task_recovery_proposals`, `agent_scorecards`, or
`overnight_summaries`.

**Cause:** Database predates migration 017.

**Fix:**

```bash
coordinator migrate
coordinator doctor --json
```

Migration `017_strategy_recovery.sql` is byte-identical in both migration roots
and ships in the installed wheel.

## Evidence review tables missing (Phase 8)

**Symptom:** `/evidence`, `/review`, `/risk`, or `/merge-ready` return RPC errors
mentioning `task_evidence`, `task_review_verdicts`, `task_risk_assessments`, or
`review_packets_v2`.

**Cause:** Database predates migration 018.

**Fix:**

```bash
coordinator migrate
coordinator doctor --json
```

Migration `018_evidence_review_gates.sql` is byte-identical in both migration
roots and ships in the installed wheel.

## Task blocked at completion evidence gate

**Symptom:** Task stays `failed` or `awaiting_human` with note mentioning
`completion evidence gate` or `missing acceptance evidence`.

**Cause:** Phase 8 requires durable evidence before `done`:

- verification commands must be recorded (failed commands block completion)
- code tasks need changed-file diff evidence
- acceptance criteria must be covered by `acceptance` evidence or rule-inferred
  coverage from passing verification + matching changed files
- risky changes (migrations, protected paths, secret-looking diffs) require human
  review per repo `review_policy`

**Fix:**

```bash
coordinator --print -p "/evidence <task-id>"
coordinator --print -p "/review <task-id>"
coordinator --print -p "/risk <task-id>"
```

Inspect blockers, fix verification or code changes, then `/retry <task-id>`.

## `/merge-ready` returns false despite green verification

**Symptom:** Verification passed but `/merge-ready` reports `merge_ready: false`
and `requires_human_review: true`.

**Cause:** Merge readiness respects repo policy and risk assessment — not
verification alone. Migration files, dependency manifests, protected paths, large
diffs, and `review_policy` values such as `risky_human` or `full_review` require
human review even when commands passed.

**Fix:**

```bash
coordinator --print -p "/risk <task-id>"
coordinator --print -p "/review <task-id>"
```

Review the packet under `.coordinator/review_packets_v2/`, then `/approve
<task-id>` when appropriate. Coordinator does not auto-merge beyond existing
repo policy.

## `/inbox` is empty but tasks need attention

**Symptom:** Tasks are `awaiting_human` or delivery failed but `/inbox` shows nothing.

**Cause:** Inbox collectors run on RPC; stale items resolve when source state clears.
The project context must match the registered project for the current git root.

**Fix:**

```bash
coordinator --print -p "/inbox"
coordinator --print -p "/attention"
coordinator --print -p "/scan"
```

Refresh by re-running `/inbox` after task or delivery state changes.

## `/notify` does not send external email or Slack

**Symptom:** `/notify` completes but no external message arrives.

**Cause:** Phase 10 uses local sinks only (`file`, `stdout`, optional `command`).
There is no live email/Discord/Slack integration.

**Fix:** Check `state/notifications.jsonl` under `COORDINATOR_HOME`, or run
`/notify --dry-run` to preview deliveries. Enable `command` sink only with
`policy.notifications.allow_command_sink = true` in `policy.toml`.

## `/deliver` blocked despite green verification

**Symptom:** `/merge-ready` or verification looks fine but `/deliver` reports
`allowed: false`.

**Cause:** Delivery enforces Phase 8 evidence gates, merge-readiness, repo
allowlist, `allow_push`, `merge_policy`, and human-review policy. It does not
push or open a PR when any blocker remains.

**Fix:**

```bash
coordinator --print -p "/merge-ready <task-id>"
coordinator --print -p "/merge-policy"
coordinator --print -p "/delivery <task-id>"
```

Resolve blockers (evidence, human review, or `allow_push=false`) before retrying
`/deliver`.

## `/ci` reports fail after delivery

**Symptom:** `/ci <task-id>` shows `ci_state: fail` and delivery status
`ci_failed`.

**Cause:** GitHub checks failed on the delivery PR. Coordinator records the
failure and may create one bounded `ci_repair` recovery proposal.

**Fix:**

```bash
coordinator --print -p "/delivery <task-id>"
coordinator --print -p "/recoveries"
```

Fix the failing checks on GitHub, then `/retry` or admit the recovery backlog
item. Coordinator does not infinite-retry CI automatically.

Phase 12 adds targeted self-healing commands:

```bash
coordinator project add <repo-path> --yes
coordinator --print -p "/ci failures"
coordinator --print -p "/heal"
```

`/heal` runs a bounded dry-run cycle (watch + classify + deduped repair).
Use `/recoveries` to admit pending `ci_repair` proposals.

## `/stale` lists a delivery PR

**Symptom:** `/stale` shows a PR health record with `stale=true`.

**Cause:** The base branch advanced after the delivery branch was created.

**Fix:**

```bash
coordinator --print -p "/rebase <delivery-id>"
coordinator --print -p "/rebase <delivery-id> --apply"
```

Dry-run is the default. Apply requires `allow_push=true` and passes human-review
policy. Force rebase is blocked unless `allow_force_update=true`.

## `/rebase` blocked or failed

**Symptom:** `/rebase <delivery-id>` returns `status: blocked` or `failed`.

**Cause:** `allow_push=false`, human review required, merge conflicts, or missing
branch.

**Fix:** Check `/merge-policy`, resolve conflicts locally, then retry dry-run.
Conflicts record healing evidence without leaving a dirty main worktree.

## `/recoveries` shows nothing after a failed task

**Symptom:** A task failed but `/recoveries` reports none pending.

**Cause:** Recovery proposals are created when the autonomous loop evaluates a
terminal failed/blocked task, or when `propose_recovery_for_failed_task` runs.
Duplicate proposals for the same task are deduped.

**Fix:**

```bash
coordinator --print -p "/loop step"
coordinator --print -p "/recoveries"
coordinator --print -p "/evals"
```

Confirm a `fail` or `blocked` evaluation exists before expecting backlog admission.

## Overnight run paused during quiet hours

**Symptom:** `/loop run` shows `paused` with reason mentioning quiet hours;
no new tasks start overnight.

**Cause:** Phase 7 pauses autonomous run ticking during the configured quiet
window. Active workers are **not** killed — they finish at a safe boundary.

**Fix:** Wait for quiet hours to end, or adjust `policy.toml`:

```toml
[overnight]
quiet_start = "22:00"
quiet_end = "08:00"
```

Then `coordinator --print -p "/loop resume"` if the session was paused.

## `/agents` shows an agent on cooldown

**Symptom:** Preferred rank is empty or an agent shows `cooldown_until` in the future.

**Cause:** Individual agent cooldowns after failures/timeouts. Other capable
agents continue to receive work.

**Fix:** Wait for cooldown to expire, or inspect scorecards with
`coordinator --print -p "/agents"`. Cooldown on one agent does not block all agents.

## Config explain shows `[REDACTED]`

**Symptom:** Effective values appear as `[REDACTED]` in `config explain` output.

**Cause:** Secret-like keys (tokens, API keys, env overrides) are intentionally
redacted in text and JSON.

**Fix:** Inspect the non-secret fields (`source_kind`, `source_path`) to see
which file or environment variable set the value. Edit the source file directly;
do not expect raw secrets in CLI output.

## Still stuck?

Gather diagnostics:

```bash
coordinator supervisor status
coordinator supervisor status --json
coordinator doctor
coordinator doctor --json
coordinator config explain --json
tail -100 ~/.local/state/coordinator/supervisor.log
python3 --version
node --version
git --version
echo "COORDINATOR_HOME=$COORDINATOR_HOME"
echo "COORDINATOR_LEGACY_ROOT=$COORDINATOR_LEGACY_ROOT"
```

## See also

- [Installation](install.md)
- [TUI operator guide](tui.md)
- [Migration](migration.md)