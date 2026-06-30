# Phase 12 PR and CI Self-Healing — Acceptance Handoff

**Branch:** `phase12-pr-ci-self-healing`  
**Base:** `main` @ Phase 11 merged  
**Status:** Ready for Codex Gate G

## Scope delivered

- Migration 022: `pr_health_records`, `pr_healing_attempts`, `ci_failure_records`
- PR watcher with fake `gh` fixtures (no live network in tests)
- CI failure classifier and deduped `ci_repair` recovery backlog
- Safe rebase controller (dry-run default, isolated worktrees, no force-push)
- Review comment ingest (untrusted external text, operator items, brain memories)
- PR evidence refresh (append latest section, preserve failure history)
- RPCs: `project.pr.health`, `project.pr.heal`, `project.pr.rebase`,
  `project.pr.reviews`, `project.pr.update_evidence`
- Slash: `/heal`, `/stale`, `/ci failures`, `/reviews`, `/pr update`, `/rebase`

## Verification (Grok)

```bash
git diff --check
PYTHONPATH=src python3 -m unittest \
  tests.test_pr_watcher \
  tests.test_rebase_controller \
  tests.test_ci_failure_classifier \
  tests.test_review_comment_ingest \
  tests.test_pr_evidence_update \
  tests.test_phase12_pr_ci_self_healing_e2e -v
PYTHONWARNINGS=error::ResourceWarning PYTHONPATH=src python3 -m unittest discover -s tests -q
npm run typecheck --prefix ui-tui
npm run lint --prefix ui-tui
npm test --prefix ui-tui -- --run
npm run build --prefix ui-tui
PYTHONPATH=src python3 -m unittest tests.test_tui_bundle tests.test_wheel_migrations -v
python3 -m build
```

**Results:** 1245/1245 Python tests pass; 154/154 ui-tui tests pass; TUI bundle rebuilt.

## Clean-wheel smoke (Gate G)

After `pip install` of the built wheel (no `PYTHONPATH`):

```bash
coordinator init --dry-run --json
coordinator init --yes --json
coordinator project add <repo-path> --yes
coordinator --print -p "/prs"
coordinator --print -p "/stale"
coordinator --print -p "/ci failures"
```

Register the project before slash commands on a fresh `COORDINATOR_HOME`.

## Gemini Gate F

See [2026-06-30-phase12-gemini-review.md](2026-06-30-phase12-gemini-review.md) — PASS.

## Out of scope

Phase 13 external approval channels — not implemented on this branch.