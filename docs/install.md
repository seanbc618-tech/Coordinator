# Installing Coordinator

Coordinator ships as a Python wheel with the TUI JavaScript bundle embedded inside
the package. After installation, the `coordinator` command is available globally
(no `PYTHONPATH` required).

## Requirements

- **Python 3.11+**
- **Node.js** on your `PATH` (the TUI runs as a Node process)
- **Git** (the no-argument launcher resolves the current repository)

## Install from a built wheel

From the Coordinator source tree:

```bash
python3 -m build
pip install --force-reinstall dist/local_cli_coordinator-*.whl
```

This is the same install command verified by the wheel packaging tests: build a
wheel, then `pip install --force-reinstall <wheel>` into a fresh virtual
environment.

### Fresh virtual environment (recommended)

```bash
python3 -m venv ~/.venvs/coordinator
source ~/.venvs/coordinator/bin/activate
python3 -m build
pip install --force-reinstall dist/local_cli_coordinator-*.whl
```

Verify the install:

```bash
coordinator supervisor status   # reports running or not running
coordinator doctor              # checks loop readiness (admin CLI)
```

## Upgrade

Rebuild the wheel and reinstall with `--force-reinstall`:

```bash
python3 -m build
pip install --force-reinstall dist/local_cli_coordinator-*.whl
```

Upgrading replaces the Python package and bundled TUI artifact in one step.

## Uninstall

```bash
pip uninstall local-cli-coordinator
```

Uninstalling removes the `coordinator` command and packaged TUI bundle. It does
**not** delete your global Coordinator data (config, database, socket, logs).
See [Global directories](#global-directories) below if you want to remove that
state manually.

## First launch

Inside any Git repository:

```bash
cd /path/to/your/project
coordinator
```

With no subcommand, Coordinator:

1. Resolves the canonical Git root for the current directory
2. Offers legacy migration if global state is empty and a legacy install is
   detected (see [migration.md](migration.md))
3. Starts or attaches to the detached Supervisor
4. Opens the TUI for the current project (onboarding if the project is new)

Administrative subcommands (`coordinator status`, `coordinator migrate`, etc.)
keep their existing CLI behavior.

## Global directories

Coordinator stores state in XDG-compliant directories:

| Purpose | Default path | Override |
|---|---|---|
| Config | `~/.config/coordinator/` | `$XDG_CONFIG_HOME/coordinator/` |
| Data (database, runs, tasks) | `~/.local/share/coordinator/` | `$XDG_DATA_HOME/coordinator/` |
| State (socket, locks, logs) | `~/.local/state/coordinator/` | `$XDG_STATE_HOME/coordinator/` |

Important files under state:

| File | Purpose |
|---|---|
| `coordinator.sock` | Unix socket for the Supervisor |
| `supervisor.lock` | Single-instance Supervisor lock |
| `supervisor.log` | Detached Supervisor stdout/stderr |
| `supervisor-startup.lock` | Atomic lock during Supervisor startup |

### Test or isolated installs

Set `COORDINATOR_HOME` to place all three directories under one root:

```bash
export COORDINATOR_HOME=/tmp/coordinator-test
```

Layout: `$COORDINATOR_HOME/config`, `$COORDINATOR_HOME/data`,
`$COORDINATOR_HOME/state`.

## Supervisor lifecycle

The TUI launcher automatically ensures a detached Supervisor is running. You can
also manage it explicitly:

```bash
# Check whether the Supervisor is reachable
coordinator supervisor status

# Graceful shutdown (stops all project scheduling)
coordinator supervisor stop

# Foreground start (mainly for debugging)
coordinator supervisor start --foreground
```

`coordinator supervisor status` prints whether the Supervisor is running and the
socket path. A second concurrent `supervisor start` is rejected while one
instance holds the lock.

## Rebuild the TUI bundle (developers)

If you change `ui-tui/` source, rebuild and reinstall:

```bash
npm run build --prefix ui-tui
pip install --force-reinstall .
```

This is the exact rebuild command embedded in bundle error messages.

## Corrupt or missing bundle recovery

If the packaged bundle is missing, has a hash mismatch, or reports an unsupported
protocol version, Coordinator prints:

```
Rebuild and reinstall with: npm run build --prefix ui-tui && pip install --force-reinstall .
```

Run that command from the Coordinator source tree, then retry `coordinator`.

Typical causes:

- Installed from an incomplete checkout (bundle not built)
- Edited `src/local_cli_coordinator/tui_bundle/` by hand
- Mixed an old wheel with a new Supervisor protocol

## Release verification (maintainers)

```bash
npm run typecheck --prefix ui-tui
npm run lint --prefix ui-tui
npm test --prefix ui-tui -- --run
npm run build --prefix ui-tui
PYTHONPATH=src python3 -m unittest discover -s tests -q
python3 -m build
git diff --check
```

Then install the wheel in a fresh venv and smoke-test:

```bash
python3 -m venv /tmp/coord-smoke-venv
/tmp/coord-smoke-venv/bin/pip install --force-reinstall dist/local_cli_coordinator-*.whl
/tmp/coord-smoke-venv/bin/coordinator supervisor status
```

## See also

- [TUI operator guide](tui.md)
- [Legacy migration](migration.md)
- [Troubleshooting](troubleshooting.md)