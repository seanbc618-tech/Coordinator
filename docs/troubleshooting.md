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