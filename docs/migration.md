# Migrating to Global Coordinator Paths

Older Coordinator installations used a single root directory (database, config,
runs, tasks, and state in one tree). The global install stores data in XDG paths
under `~/.config/coordinator`, `~/.local/share/coordinator`, and
`~/.local/state/coordinator`.

Migration copies your legacy state into the global layout. **The original source
directory is never deleted automatically.**

## What gets migrated

From the legacy root, Coordinator copies any of these that exist:

| Legacy path | Global destination |
|---|---|
| `coordinator.db` | `~/.local/share/coordinator/coordinator.db` |
| `config/` | `~/.config/coordinator/` |
| `runs/` | `~/.local/share/coordinator/runs/` |
| `tasks/` | `~/.local/share/coordinator/tasks/` |
| `state/` | `~/.local/state/coordinator/` |

Artifact paths inside the database are remapped to the new data directory after
migration.

## Detecting a legacy installation

Coordinator looks for a legacy root when global state is still empty:

1. **`COORDINATOR_LEGACY_ROOT`** — explicit path (highest priority)
2. **Development checkout** — the Coordinator source tree when run from a dev
   install and global paths are empty

If `COORDINATOR_HOME` is set, automatic legacy detection is disabled (use the
administrative `migrate` command instead).

## First-run interactive migration

The first time you run `coordinator` with empty global state and a detectable
legacy root, Coordinator prints a summary and asks for confirmation:

```
Legacy Coordinator installation detected.
Source: /path/to/legacy
Target: /Users/you/.local/share

The following will be migrated:
  - coordinator.db
  - config
  ...

A timestamped backup is created before overwriting existing global state.
The original installation is never deleted automatically.

validation: dry_run
Migrate legacy Coordinator state? [y/N]
```

Type `y` or `yes` to proceed. Any other answer declines migration and exits.

Non-interactive first launch (no TTY) **refuses** to migrate automatically. Use
the administrative command below.

## Administrative migration

### Dry run (validate only)

Validates the source database and layout without writing to global directories:

```bash
coordinator migrate --source /path/to/legacy --dry-run
```

Expected output: `dry run: dry_run`

The dry run copies the database to a temporary file, runs schema validation, and
confirms the source hash is unchanged.

### Full migration

Requires explicit confirmation:

```bash
coordinator migrate --source /path/to/legacy --yes
```

On success:

```
status: migrated
backup: /Users/you/.local/share/backup-20260623T120000Z
```

If the source was already migrated, status is `already_migrated`.

## Backups

Before overwriting existing global directories, Coordinator creates a timestamped
backup:

```
~/.local/share/backup-<UTC-timestamp>/
├── config/
├── data/
└── state/
```

Each backup contains only the directories that existed before migration. The
migration result prints the backup path when one is created.

### Finding your backup

List backup directories:

```bash
ls -d ~/.local/share/backup-*
```

Check the migration marker after a successful run:

```bash
cat ~/.local/share/coordinator/.migrated
```

The marker records the source path, database hash, and completion time.

## Validation

Migration validates staged data before promoting it to live directories:

1. Copy legacy files into per-target staging directories (same filesystem as
   each target, so renames are atomic)
2. Run `init_db` on the staged database copy
3. Promote staging → live with a rename chain
4. Remap artifact paths and write the `.migrated` marker

If validation fails during a dry run, no global directories are modified.

## Automatic rollback on failure

If migration fails after backups are taken, Coordinator:

1. Restores pre-existing directories from the timestamped backup
2. Removes newly created directories that did not exist before migration
3. Clears the migration journal
4. Leaves the **legacy source untouched**

You do not need to run a separate rollback command for failed migrations.

## Manual rollback after success

To revert a **completed** migration:

1. Stop the Supervisor: `coordinator supervisor stop`
2. Locate the backup: `ls -d ~/.local/share/backup-*`
3. Restore each directory:

```bash
BACKUP=~/.local/share/backup-20260623T120000Z

# Remove current global state
rm -rf ~/.config/coordinator ~/.local/share/coordinator ~/.local/state/coordinator

# Restore from backup
cp -a "$BACKUP/config" ~/.config/coordinator
cp -a "$BACKUP/data" ~/.local/share/coordinator
cp -a "$BACKUP/state" ~/.local/state/coordinator

# Clear the migration marker if you want first-run detection again
rm -f ~/.local/share/coordinator/.migrated
```

4. Continue using the legacy source directory as before — it was never deleted.

## Interrupted migration recovery

Migration progress is recorded in a journal:

```
~/.local/share/.migration-journal.json
```

If migration is interrupted (power loss, killed process), re-run the same
`coordinator migrate --source … --yes` command. Coordinator resumes from the
journal when the source matches.

**Corrupt journal:** delete `~/.local/share/.migration-journal.json` and retry, or
restore from backup.

**Journal source mismatch:** if the journal references a different source path,
delete the journal before starting a new migration.

## Guarantee: source is preserved

Migration never deletes the legacy root. Tests and production code enforce this:

- Files are copied, not moved
- The source `coordinator.db` hash is checked after migration
- Operators can keep the old tree as an archive indefinitely

## Environment variables

| Variable | Purpose |
|---|---|
| `COORDINATOR_LEGACY_ROOT` | Point first-run detection at a specific legacy root |
| `COORDINATOR_HOME` | Isolated global paths; disables automatic legacy detection |

## See also

- [Installation](install.md)
- [TUI operator guide](tui.md)
- [Troubleshooting](troubleshooting.md)