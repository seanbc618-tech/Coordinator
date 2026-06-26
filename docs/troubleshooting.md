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

## Still stuck?

Gather diagnostics:

```bash
coordinator supervisor status
coordinator doctor
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