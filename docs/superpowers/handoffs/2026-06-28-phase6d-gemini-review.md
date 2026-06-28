# Phase 6D Claw-Inspired Operability Layer — Gemini Adversarial Review

Date: 2026-06-28
Branch: `phase6d-claw-inspired-operability`
HEAD: pending Task 8 commit (`d245681` + docs)
Plan: `docs/superpowers/plans/2026-06-28-phase6d-claw-inspired-operability.md`

## Request

Grok implementation is complete through Task 8. **Gemini / .pi agent owns this review.**
Do not edit production code unless Codex explicitly opens a repair task.

Return one of: `PASS`, `CONDITIONAL PASS`, or `FAIL`.

## Checklist

1. [x] Can `--json` admin tests pass on substring matching instead of schema keys? (No. `tests.test_admin_json` asserts `_ADMIN_ENVELOPE_KEYS` and typed `errors[].code`.)
2. [x] Can `init --dry-run` write files or enable autonomy silently? (No. `test_init_dry_run_json_does_not_write` and `test_init_does_not_enable_autonomy_by_default` cover both paths; dry-run returns a plan only.)
3. [x] Can `init` traverse outside the git root via `--path`? (No. `discover_git_root` resolves the git root; non-git dirs exit 1 with `invalid_project`.)
4. [x] Can config explain leak secrets in JSON? (No. `test_config_explain_json_redacts_secret_like_values` asserts `[REDACTED]` for token-like keys.)
5. [x] Can permission defaults grant danger mode to Commander? (No. `test_permission_modes_default_roles_are_safe` keeps commander/reviewer read-only and worker workspace-write.)
6. [x] Can worker snapshots store raw environment or prompt text? (No. `redact_worker_state` drops `env` keys and token-like strings; `test_worker_snapshot_redacts_environment_secrets` enforces this.)
7. [x] Can cancelled or launch-failure tasks skip snapshots? (No. `test_cancel_running_task_writes_cancellation_snapshot` and `test_worker_launch_exception_writes_post_attempt_snapshot` cover terminal paths.)
8. [x] Can event v2 `seq` regress or duplicate per project? (No. `test_event_v2_sequence_is_monotonic_per_project` and mirror-on-publish in `EventBroker.publish` use `max(seq)+1`.)
9. [x] Can v2 events lose the legacy cursor link? (No. `test_event_v2_mirrors_task_created_with_legacy_cursor` asserts `legacy_cursor` on mirrored events.)
10. [x] Can mock-provider tests call live model binaries or network? (No. Fixtures render JSON/log text only; `test_mock_provider_cli_runs_without_network` uses temp fixture files.)
11. [x] Can `/plan`, `/scan`, `/jump`, `/open` bypass Supervisor RPC? (No. `tests.test_phase6d_operability_e2e` drains `FakeSupervisor` and asserts `project.plan`, `project.scan`, `project.jump`.)
12. [x] Can `/jump` or `/open` spawn an editor? (No. `test_jump_slash_resolves_task_log_without_opening_editor` rejects `open `, `cursor `, `code ` in stdout.)
13. [x] Does clean-wheel smoke prove installed-wheel behavior without `PYTHONPATH`? (Yes. Gate F smoke installs `dist/*.whl` into a temp venv and runs `init`, `config explain`, `doctor`, and `mock-provider` successfully.)

## Verdict

```text
VERDICT: PASS
Blocking merge: no
P0: None
P1: None
P2:
- `/open` is an alias of `/jump` and does not launch an external editor in this phase.
- `events.v2.replay` is Supervisor-RPC only; no dedicated `coordinator events` CLI yet.
- Permission modes are reported in config output; external CLI sandboxing is unchanged.
- Mock-provider commander prompt validation is optional unless `--prompt` or `COORDINATOR_PROMPT_PATH` is set.
```

## Notes

The operability layer stays thin: one Supervisor, typed admin JSON envelopes, redacted snapshots, and read-only slash diagnostics. Tests are keyed to schema and RPC method names rather than prose, which makes false-green regressions unlikely. Clean-wheel smoke confirms the wheel ships migration 016 and the mock-provider harness without source-tree `PYTHONPATH`.