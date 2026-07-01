# Phase 20 Release Extension Layer — Acceptance Handoff

**Branch:** `phase16-20-implementation`  
**Base:** `main` @ Phase 15 merged  
**Status:** Ready for Gemini Gate F + Codex Gate G

## Scope delivered

- Migration 030: `backup_runs`, `restore_runs`, `upgrade_preflight_runs`, `extension_manifests`
- Coordinator home backup with manifest and checksum verification
- Restore dry-run default; apply with schema compatibility checks and optional force
- Upgrade preflight with migration backup guidance
- Declarative local extension manifests (no arbitrary code execution)
- Release checklist helpers for clean-wheel verification
- CLI/RPC: `backup create`, `backup verify`, `restore`, `upgrade preflight`, `extensions list`, `release check`

## Gemini design red lines (tests)

- Restore dry-run writes nothing
- Tampered backup checksum fails verification
- Incompatible schema restore refused without force
- Extension loader rejects executable payloads and path escapes

## Verification (Grok)

```bash
git diff --check
PYTHONPATH=src python3 -m unittest \
  tests.test_backup_manager \
  tests.test_upgrade_preflight \
  tests.test_extension_manifest \
  tests.test_extension_loader \
  tests.test_release_checks \
  tests.test_phase20_release_e2e -v
PYTHONWARNINGS=error::ResourceWarning PYTHONPATH=src python3 -m unittest discover -s tests -q
npm run typecheck --prefix ui-tui
npm run lint --prefix ui-tui
npm test --prefix ui-tui -- --run
npm run build --prefix ui-tui
PYTHONPATH=src python3 -m unittest tests.test_tui_bundle tests.test_wheel_migrations -v
python3 -m build
```

## Clean-wheel smoke (Gate G)

After `pip install` of the built wheel (no `PYTHONPATH`):

```bash
coordinator init --yes --json
coordinator backup create --json
coordinator upgrade preflight --json
coordinator extensions list --json
coordinator release check --json
```

`release check` must return `ok=true` on a wheel install without a source-tree `migrations/` mirror.

## Out of scope

Cloud backup, silent overwrite restore, auto-upgrade without approval, public extension marketplace.