# Phase 5.1 TUI Task Visibility and Worker Reliability — Grok Handoff

Date: 2026-06-23
Plan: `docs/superpowers/plans/2026-06-23-phase5-1-tui-task-visibility-worker-reliability.md`
Branch: `external/coordinator-global-tui`
Baseline: Phase 5 signed at `139356f`

## Objective

Fix real-smoke gaps after Phase 5: task visibility in TUI, local `/help`, duplicate
chat echo, worktree-local worker prompts, report-only task semantics, failure
reason surfacing.

## Delivered Changes

| Area | Summary |
|------|---------|
| `project.task` RPC | Scoped task detail: goal, criteria, verify commands, latest event/attempt, artifacts |
| `project.tasks` | Adds `goal`, `latest_note` |
| `task.created` events | Enriched payload from Commander admission |
| TUI | `/task`, local `/help`, no optimistic chat echo, reducer dedupe, richer ActivityBlock |
| Engine | Prompt under `.coordinator/<task-id>/prompt.md`, git exclude, report-only verify path |
| Git | Filter `.coordinator/` from changed-file collection |
| Supervisor | Publish `task.done` with `reason` after project cycles |
| Tests | Supervisor, engine, vitest, PTY gates |

## Verification Commands

```bash
npm run typecheck --prefix ui-tui
npm run lint --prefix ui-tui
npm test --prefix ui-tui -- --run
PYTHONPATH=src python3 -m unittest \
  tests.test_supervisor_methods.ProjectSlashMethodsTest \
  tests.test_supervisor_commander.ChatSendBridgeTests \
  tests.test_engine.EngineTests.test_report_only_task_passes_without_changed_files \
  tests.test_engine.EngineTests.test_worker_prompt_is_written_inside_worktree \
  tests.test_tui_pty.TuiPtyTests.test_pty_help_shows_commands_without_rpc \
  tests.test_tui_pty.TuiPtyTests.test_pty_chat_shows_user_message_once \
  tests.test_tui_pty.TuiPtyTests.test_pty_task_shows_baseline_detail \
  -v
npm run build --prefix ui-tui
```

Full Python suite (isolated XDG recommended):

```bash
XDG_CONFIG_HOME=/private/tmp/coordinator-phase5-1-xdg/config \
XDG_DATA_HOME=/private/tmp/coordinator-phase5-1-xdg/data \
XDG_STATE_HOME=/private/tmp/coordinator-phase5-1-xdg/state \
PYTHONWARNINGS=error::ResourceWarning \
PYTHONPATH=src python3 -m unittest discover -s tests -q
```

## Manual Smoke

```bash
cd /Users/xiafan/polymarket-crypto-threshold
coordinator
/help
/tasks
/task <latest-task-id>
hi，请生成 1 个很小的后续任务
/task <new-task-id>
/quit
```

## Gemini Review Checklist

See plan section **Gemini Adversarial Review Checklist** — verify task scope
safety, enriched visibility, local `/help`, single user message, worktree prompts,
report-only semantics, regression gates.