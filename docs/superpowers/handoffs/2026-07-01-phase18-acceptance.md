# Phase 18 Evidence Artifact Warehouse — Acceptance Handoff

**Branch:** `phase16-20-implementation`  
**Base:** `main` @ Phase 15 merged  
**Status:** Ready for Gemini Gate F + Codex Gate G

## Scope delivered

- Migration 028: warehouse `artifacts` registry, `evidence_exports`, `retention_runs` (legacy `artifacts` renamed to `task_artifacts`)
- Canonical artifact registration with checksums, provenance, and redaction status
- Project-scoped evidence search with secret redaction
- Export bundles with manifest and checksums
- Retention planning: dry-run default; apply exports exact deletion candidates before unlink
- CLI/RPC/slash: `/evidence`, `/artifacts`, `/export evidence`, `/retention`

## Gemini design red lines (tests)

- Path traversal blocked via canonical roots
- Export redacts secrets in text artifacts
- Retention apply exports exact stale candidates (not newest N)
- Deletes skipped unless `artifact_id` present in export manifest
- DB history preserved after retention apply

## Verification (Grok)

```bash
git diff --check
PYTHONPATH=src python3 -m unittest \
  tests.test_artifact_registry \
  tests.test_evidence_search \
  tests.test_evidence_export \
  tests.test_retention_policy \
  tests.test_phase18_evidence_warehouse_e2e -v
PYTHONWARNINGS=error::ResourceWarning PYTHONPATH=src python3 -m unittest discover -s tests -q
npm run typecheck --prefix ui-tui
npm run lint --prefix ui-tui
npm test --prefix ui-tui -- --run
npm run build --prefix ui-tui
PYTHONPATH=src python3 -m unittest tests.test_tui_bundle tests.test_wheel_migrations -v
python3 -m build
```

## Clean-wheel smoke (Gate G)

```bash
coordinator --print -p "/artifacts"
coordinator --print -p "/evidence"
coordinator --print -p "/retention"
```

## Out of scope

Cloud storage, external search services, deleting DB history by default.