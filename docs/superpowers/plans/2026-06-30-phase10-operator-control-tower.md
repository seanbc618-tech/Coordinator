# Phase 10 Operator Control Tower Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the user one trusted command center for all projects: what needs human attention, what failed overnight, what is ready to deliver, what is blocked, and what notification or approval action should happen next.

**Architecture:** Add an operator layer above tasks, autonomous runs, evidence review, delivery records, and recovery proposals. It reads existing durable state, normalizes it into project-scoped operator inbox items, applies notification policy and dedupe, exposes Supervisor RPCs, and renders `/inbox`, `/attention`, `/notify`, `/summary`, and `/decision` in CLI/TUI. It must not mutate task state except through existing approved RPCs such as approve/retry/cancel/deliver.

**Tech Stack:** Python `unittest`, SQLite migration 020 mirrored in both migration roots, existing Supervisor RPC protocol, existing admin JSON envelope, TypeScript/Vitest slash routing and rendering, fake notification sinks only in tests, clean-wheel smoke without `PYTHONPATH`.

---

## 0. Why Phase 10 Exists

Coordinator can now do local work, review evidence, run autonomous loops, and open evidence-backed PRs. The remaining operator problem is attention.

Right now the user still has to ask many separate questions:

- Which projects need me?
- Which tasks are awaiting human review?
- Which CI failures produced recovery proposals?
- Which delivery records are ready or blocked?
- Which autonomous run paused, failed, or went quiet?
- What happened overnight?

Phase 10 turns those scattered states into one control tower.

---

## 1. Role Assignment

| Role | Owner | Responsibility |
| --- | --- | --- |
| Main implementation | Grok | Own Tasks 1-9, one commit per task, stop at Codex/Gemini gates. |
| Adversarial review | Gemini / `.pi agent` | Review Gates B, D, F. Focus on cross-project leaks, fake notification success, unsafe approval, duplicate alerts, and misleading summaries. |
| Optional support | Claude Code | Red tests, docs, fixture JSON only. No state-machine, notification-policy, approval, or RPC implementation. |
| Gate owner | Codex | Gate A/C/E/G independent verification and final sign-off. |

---

## 2. Scope Table

| Area | In Scope | Out of Scope |
| --- | --- | --- |
| Operator inbox | Normalize actionable states from tasks, reviews, recoveries, delivery, CI, runs, and config | New task execution engine |
| Human decisions | Show decision items and route actions through existing safe RPCs | Bypassing review, merge, push, or cancellation policy |
| Notifications | Durable delivery records, dedupe, local file sink, optional command sink disabled by default | Live email/Gmail/Discord/Slack integrations |
| Summaries | Per-project and global morning summary from durable events | LLM-generated prose summaries as the only source of truth |
| TUI/CLI | `/inbox`, `/attention`, `/summary`, `/notify`, `/decision` | New web dashboard |
| Safety | Project scoping, redaction, dry-run first for side effects | Silent auto-approval |

---

## 3. File Map

Create:

- `migrations/020_operator_control_tower.sql`
- `src/local_cli_coordinator/migrations/020_operator_control_tower.sql`
- `src/local_cli_coordinator/operator_inbox.py`
- `src/local_cli_coordinator/operator_summary.py`
- `src/local_cli_coordinator/notification_policy.py`
- `src/local_cli_coordinator/notification_sinks.py`
- `tests/test_operator_inbox.py`
- `tests/test_operator_summary.py`
- `tests/test_notification_policy.py`
- `tests/test_notification_sinks.py`
- `tests/test_phase10_operator_e2e.py`
- `docs/superpowers/handoffs/2026-06-30-phase10-gemini-review.md`
- `docs/superpowers/handoffs/2026-06-30-phase10-acceptance.md`

Modify:

- `src/local_cli_coordinator/db.py`
- `src/local_cli_coordinator/supervisor_methods.py`
- `src/local_cli_coordinator/cli_chat.py`
- `src/local_cli_coordinator/admin_json.py`
- `src/local_cli_coordinator/config.py`
- `src/local_cli_coordinator/config_explain.py`
- `ui-tui/src/slash.ts`
- `ui-tui/src/slashRpc.ts`
- `ui-tui/src/slashDisplay.ts`
- `docs/cli.md`
- `docs/tui.md`
- `docs/troubleshooting.md`
- `README.md`
- `tests/test_migration_mirror_sync.py`
- `tests/test_supervisor_methods.py`
- `tests/test_cli_prompt.py`
- `tests/test_tui_pty.py`

---

## 4. Data Model

Migration `020_operator_control_tower.sql` must be byte-identical in both migration roots.

```sql
CREATE TABLE IF NOT EXISTS operator_items (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    action_label TEXT NOT NULL DEFAULT '',
    action_method TEXT,
    action_params_json TEXT NOT NULL DEFAULT '{}',
    dedupe_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_operator_items_dedupe
ON operator_items(project_id, dedupe_key)
WHERE status IN ('open', 'acknowledged');

CREATE INDEX IF NOT EXISTS idx_operator_items_project_status
ON operator_items(project_id, status, severity, updated_at);

CREATE TABLE IF NOT EXISTS notification_rules (
    id TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1,
    sink TEXT NOT NULL,
    min_severity TEXT NOT NULL DEFAULT 'warning',
    project_id TEXT,
    event_filter TEXT NOT NULL DEFAULT '*',
    quiet_start TEXT,
    quiet_end TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notification_deliveries (
    id TEXT PRIMARY KEY,
    rule_id TEXT NOT NULL,
    operator_item_id TEXT,
    project_id TEXT NOT NULL,
    sink TEXT NOT NULL,
    status TEXT NOT NULL,
    dedupe_key TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_notification_delivery_dedupe
ON notification_deliveries(rule_id, dedupe_key);

CREATE TABLE IF NOT EXISTS operator_summaries (
    id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    project_id TEXT,
    summary_kind TEXT NOT NULL,
    from_cursor INTEGER,
    to_cursor INTEGER,
    counts_json TEXT NOT NULL DEFAULT '{}',
    highlights_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
```

Allowed enum values:

- `operator_items.severity`: `info`, `warning`, `error`, `critical`
- `operator_items.status`: `open`, `acknowledged`, `resolved`, `dismissed`
- `operator_items.source_type`: `task`, `review`, `risk`, `delivery`, `ci`, `recovery`, `run`, `config`, `supervisor`
- `notification_deliveries.status`: `sent`, `skipped`, `failed`
- `notification_rules.sink`: `file`, `stdout`, `command`

---

## 5. Task Table

| Task | Owner | Commit Message | Scope | Tests |
| --- | --- | --- | --- | --- |
| 0 | Claude optional or Grok | `test: capture operator control tower contracts` | Red tests for inbox normalization, dedupe, notification safety, summaries, RPCs, slash routing. | Phase 10 focused tests fail for missing implementation only. |
| 1 | Grok | `feat: persist operator inbox state` | Migration 020, mirrored migrations, CRUD helpers, enum validation, dedupe behavior. | `tests.test_operator_inbox tests.test_migration_mirror_sync` |
| 2 | Grok | `feat: normalize attention items from durable state` | `operator_inbox.py` collectors for tasks, review/risk, delivery/CI, recovery, autonomous runs, config readiness. | `tests.test_operator_inbox` |
| 3 | Grok | `feat: build operator summaries` | `operator_summary.py` global/project summaries with counts and redacted highlights. | `tests.test_operator_summary` |
| 4 | Grok | `feat: add notification policy and sinks` | `notification_policy.py`, `notification_sinks.py`, file/stdout sinks, disabled-by-default command sink, dedupe, quiet hours. | `tests.test_notification_policy tests.test_notification_sinks` |
| 5 | Grok | `feat: expose operator Supervisor RPCs` | Add `operator.inbox`, `operator.attention`, `operator.summary`, `operator.notify`, `operator.decision`, `operator.dismiss`. | `tests.test_supervisor_methods tests.test_phase10_operator_e2e` |
| 6 | Grok | `feat: route operator slash commands` | Add `/inbox`, `/attention`, `/summary`, `/notify`, `/decision`, `/dismiss` to headless CLI and TUI. | `tests.test_cli_prompt tests.test_tui_pty`, `npm test --prefix ui-tui -- --run` |
| 7 | Grok | `feat: add safe decision actions` | Route approve/retry/cancel/deliver decisions through existing RPCs with dry-run payloads and confirmation requirements. | `tests.test_phase10_operator_e2e tests.test_task_control tests.test_github_delivery` |
| 8 | Grok | `feat: add morning summary command` | Add `/summary morning` and `coordinator operator summary --json` using durable events and summaries. | `tests.test_operator_summary tests.test_cli_prompt` |
| 9 | Gemini | `docs: record Phase 10 adversarial review` | Read-only adversarial review. No production edits. | Review checklist below. |
| 10 | Grok | `docs: document Phase 10 operator control tower` | Update README, CLI/TUI/troubleshooting docs, acceptance handoff. | Full Gate G commands. |

---

## 6. Required Behaviors

### 6.1 Operator Inbox Items

Each item must be deterministic and project-scoped.

Examples:

```json
{
  "id": "opitem-...",
  "project_id": "proj-abc",
  "source_type": "delivery",
  "source_id": "12",
  "severity": "error",
  "status": "open",
  "title": "CI failed for task-abc delivery",
  "summary": "PR #42 has failing checks. A bounded recovery proposal is available.",
  "action_label": "Open recovery proposal",
  "action_method": "project.recoveries",
  "action_params": {"delivery_id": 12}
}
```

Collectors must create or update items for:

- tasks in `awaiting_human`, `failed`, `blocked`, or `running` beyond timeout;
- review packets with blockers;
- risk results requiring human review;
- delivery records in `ci_failed`, `blocked`, or `ready`;
- recovery proposals in `pending`;
- autonomous run sessions in `paused`, `failed`, or `expired`;
- readiness/config blockers from `doctor`.

Resolved source state should mark matching open item `resolved`, not delete it.

### 6.2 Notification Policy

Notifications are durable and deduped. A skipped notification is still recorded
with `status = 'skipped'` and a reason in `error`.

Rules:

- `file` sink appends JSONL to `state/notifications.jsonl`.
- `stdout` sink only emits when explicitly invoked by CLI or test harness.
- `command` sink is disabled unless `notification_rules.enabled = 1` and
  `policy.notifications.allow_command_sink = true`.
- command sink receives JSON payload on stdin, never shell-interpolated args.
- no notification includes raw prompts, context file bodies, env vars, tokens,
  or full logs.
- quiet hours suppress warning/info, but not critical.

### 6.3 Operator Decisions

`operator.decision` never mutates state directly. It translates a chosen
operator item into an existing safe RPC call:

| Source | Decision | Routed Method |
| --- | --- | --- |
| Human review task | approve | `project.task.approve` |
| Failed task | retry | `project.task.retry` |
| Running task | cancel | `project.task.cancel` with existing confirmation flow |
| Delivery ready | deliver | `project.deliver` |
| Recovery proposal | view | `project.recoveries` |
| Config blocker | explain | `project.scan` or `coordinator config explain` hint |

If an action is destructive, `operator.decision` returns `requires_confirmation`
and does not perform the action until the existing confirmation token/path is
provided.

---

## 7. Gate Schedule

### Gate A — Red-Test Quality

Owner: Codex after Task 0.

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_operator_inbox \
  tests.test_operator_summary \
  tests.test_notification_policy \
  tests.test_notification_sinks \
  tests.test_phase10_operator_e2e -v
```

Reject if tests pass before implementation, assert only display strings, skip
cross-project isolation, or require live external notification services.

### Gate B — Inbox Persistence And Normalization

Owner: Gemini after Task 2.

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_operator_inbox \
  tests.test_migration_mirror_sync -v
```

Reject if items are not project-scoped, dedupe can hide different source items,
resolved source state leaves stale open items, or migration 020 is not mirrored.

### Gate C — Summary And Notification Safety

Owner: Codex after Task 4.

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_operator_summary \
  tests.test_notification_policy \
  tests.test_notification_sinks \
  tests.test_overnight \
  tests.test_event_schema_v2 -v
```

Reject if summaries leak prompts/secrets/log bodies, command sink uses
`shell=True`, quiet hours suppress critical events, or duplicate notifications
are sent for the same dedupe key.

### Gate D — RPC And Decision Safety

Owner: Gemini after Task 7.

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_supervisor_methods \
  tests.test_phase10_operator_e2e \
  tests.test_task_control \
  tests.test_github_delivery -v
```

Reject if `operator.decision` directly edits task/delivery tables, bypasses
confirmation, leaks another project item, approves/delivers against policy, or
turns blocked delivery into success.

### Gate E — CLI And TUI Surface

Owner: Codex after Task 8.

```bash
PYTHONPATH=src python3 -m unittest tests.test_cli_prompt tests.test_tui_pty tests.test_phase10_operator_e2e -v
npm run typecheck --prefix ui-tui
npm run lint --prefix ui-tui
npm test --prefix ui-tui -- --run
```

Reject if slash commands bypass Supervisor RPC, `/inbox` shows another project's
task titles, `/notify` implies live external delivery, or TUI destructive
actions lack confirmation.

### Gate F — Gemini Final Adversarial Review

Owner: Gemini after Task 10.

Gemini must answer:

1. Can `/inbox` leak task titles or evidence from another project?
2. Can a stale operator item remain open after the source is resolved?
3. Can two different failures collapse into one dedupe key?
4. Can notification command sink run without explicit enablement?
5. Can command sink be shell-injected?
6. Can summaries leak prompts, tokens, env vars, or log bodies?
7. Can quiet hours suppress critical items?
8. Can `operator.decision` approve, retry, cancel, or deliver without using existing policy-gated RPCs?
9. Can a destructive decision execute without confirmation?
10. Are README, CLI docs, TUI docs, and actual slash behavior consistent?

### Gate G — Final Codex Sign-Off

Owner: Codex after Gemini PASS.

```bash
git diff --check
PYTHONPATH=src python3 -m unittest \
  tests.test_operator_inbox \
  tests.test_operator_summary \
  tests.test_notification_policy \
  tests.test_notification_sinks \
  tests.test_phase10_operator_e2e -v
PYTHONWARNINGS=error::ResourceWarning PYTHONPATH=src python3 -m unittest discover -s tests -q
npm run typecheck --prefix ui-tui
npm run lint --prefix ui-tui
npm test --prefix ui-tui -- --run
npm run build --prefix ui-tui
PYTHONPATH=src python3 -m unittest tests.test_tui_bundle tests.test_wheel_migrations -v
python3 -m build
```

Clean-wheel smoke without `PYTHONPATH`:

```bash
python3 -m venv /tmp/coord-phase10-venv
/tmp/coord-phase10-venv/bin/pip install dist/*.whl
env -u PYTHONPATH /tmp/coord-phase10-venv/bin/coordinator init --dry-run --json
env -u PYTHONPATH COORDINATOR_HOME=/tmp/coord-phase10-home /tmp/coord-phase10-venv/bin/coordinator init --yes --json
env -u PYTHONPATH COORDINATOR_HOME=/tmp/coord-phase10-home /tmp/coord-phase10-venv/bin/coordinator doctor --json
env -u PYTHONPATH COORDINATOR_HOME=/tmp/coord-phase10-home /tmp/coord-phase10-venv/bin/coordinator --print -p "/summary"
```

---

## 8. Expected User-Facing Result

After Phase 10, the user should be able to run:

```bash
coordinator --print -p "/inbox"
coordinator --print -p "/attention"
coordinator --print -p "/summary"
coordinator --print -p "/summary morning"
coordinator --print -p "/notify --dry-run"
coordinator --print -p "/decision opitem-abc"
coordinator --print -p "/dismiss opitem-abc"
coordinator --mode json --print -p "/inbox"
```

And understand:

- which projects need human attention;
- which failures already have recovery proposals;
- which PRs are ready, blocked, or CI-failed;
- which tasks are waiting for review;
- what happened overnight;
- which action is safe to take next.

---

## 9. Copy-Paste Dispatch

### To Grok

```text
Implement Phase 10 from docs/superpowers/plans/2026-06-30-phase10-operator-control-tower.md.
You are main implementer. One commit per task. Stop at Codex Gates A/C/E/G and Gemini Gates B/D/F.
Do not add live external notification integrations. Use fake/local notification sinks only. Do not bypass existing task, review, delivery, or cancellation RPCs.
Start with Task 0 red tests unless valid red tests already exist.
```

### To Gemini

```text
You own adversarial review for Phase 10. Review only; do not patch production code unless Codex opens a repair task.
Use the Gate F checklist in docs/superpowers/plans/2026-06-30-phase10-operator-control-tower.md.
Return PASS / CONDITIONAL PASS / FAIL with exact blockers.
Focus on cross-project leaks, stale operator items, duplicate notification dedupe, command sink safety, and unsafe decision routing.
```

### To Claude Code, If Used

```text
You may do Phase 10 Task 0 red tests or Task 10 docs/fixtures only.
Do not modify operator decision routing, notification command sink safety, cross-project scoping, or Supervisor RPC implementation.
Keep commits small and hand off to Grok for implementation.
```

---

## 10. Acceptance Criteria

Phase 10 is complete only when:

- operator inbox items are durable, deduped, project-scoped, and resolvable;
- inbox collectors cover tasks, reviews, risk, delivery, CI, recovery, runs, and config blockers;
- notification rules and deliveries are durable and deduped;
- command notification sink is disabled by default and never shell-interpolates input;
- summaries are deterministic and redacted;
- `operator.decision` routes through existing policy-gated RPCs only;
- `/inbox`, `/attention`, `/summary`, `/notify`, `/decision`, and `/dismiss` work in CLI/TUI;
- full Python, TUI, build, wheel, and clean-wheel smoke gates pass;
- Gemini adversarial review and Codex Gate G both sign off.
