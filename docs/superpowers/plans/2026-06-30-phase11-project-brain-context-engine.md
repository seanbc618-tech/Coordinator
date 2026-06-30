# Phase 11 Project Brain and Context Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Coordinator a durable per-project brain so Commander, workers, CLI, and TUI can understand repository structure, important files, test commands, prior failures, and task-specific context before assigning work.

**Architecture:** Add a project-scoped indexing and context layer under the existing Supervisor. The brain stores compact, redacted, citation-backed knowledge cards and builds bounded context packets for Commander and worker prompts. It must be deterministic, cacheable by git state, safe for secrets, and never become the only source of truth for task status.

**Tech Stack:** Python `unittest`, SQLite migration 021 mirrored in both migration roots, existing project registry, existing context file redaction, existing Commander prompt pipeline, Supervisor RPC, TypeScript/Vitest slash rendering, clean-wheel smoke without `PYTHONPATH`. Tests use only local fixtures.

---

## 0. Why Phase 11 Exists

Coordinator can now execute loops, create tasks, review evidence, deliver PRs, and surface operator attention. The remaining intelligence gap is project understanding.

Current failure modes:

- Commander creates vague or duplicate tasks because it does not know the repo shape.
- Workers receive prompts without enough local architecture context.
- Failed tasks are remembered as task rows, but not converted into reusable project knowledge.
- The user cannot ask "where should this change go?" or "why did this fail before?" in a reliable way.

Phase 11 turns durable repo understanding into a first-class subsystem.

---

## 1. Role Assignment

| Role | Owner | Responsibility |
| --- | --- | --- |
| Main implementation | Grok | Own Tasks 0-10, one commit per task, stop at Codex gates. |
| Adversarial review | Gemini / `.pi agent` | Review Gates B, D, F and final readiness. Focus on stale context, secret leakage, fake intelligence, cross-project leakage, and prompt bloat. |
| Gate owner | Codex | Gate A/C/E/G independent verification and final sign-off. |
| Claude Code | Unavailable | Do not assign Phase 11 tasks to Claude Code. |

---

## 2. Scope Table

| Area | In Scope | Out of Scope |
| --- | --- | --- |
| Project indexing | Files, docs, package metadata, scripts, tests, config, migrations, entrypoints | Full semantic code search engine |
| Knowledge cards | Component summaries, test commands, invariants, known hazards, previous failures | Long-form generated wiki |
| Context packets | Bounded task-specific context with citations and redaction | Dumping whole files into prompts |
| CLI/TUI | `/brain`, `/map`, `/where`, `/why`, `/impact`, `/context` | Web dashboard |
| Memory updates | Convert task completions/failures/reviews into durable facts | Autonomous code edits from memory alone |
| Safety | Redaction, max size, git revision cache invalidation, project scope checks | Indexing secrets, `.env`, private keys, raw logs |

---

## 3. File Map

Create:

- `migrations/021_project_brain.sql`
- `src/local_cli_coordinator/migrations/021_project_brain.sql`
- `src/local_cli_coordinator/project_brain.py`
- `src/local_cli_coordinator/project_indexer.py`
- `src/local_cli_coordinator/context_packets.py`
- `src/local_cli_coordinator/impact_analysis.py`
- `tests/test_project_brain.py`
- `tests/test_project_indexer.py`
- `tests/test_context_packets.py`
- `tests/test_impact_analysis.py`
- `tests/test_phase11_project_brain_e2e.py`
- `docs/superpowers/handoffs/2026-06-30-phase11-gemini-review.md`
- `docs/superpowers/handoffs/2026-06-30-phase11-acceptance.md`

Modify:

- `src/local_cli_coordinator/db.py`
- `src/local_cli_coordinator/supervisor_commander.py`
- `src/local_cli_coordinator/commander_runner.py`
- `src/local_cli_coordinator/supervisor_methods.py`
- `src/local_cli_coordinator/cli_chat.py`
- `src/local_cli_coordinator/context_files.py`
- `ui-tui/src/slash.ts`
- `ui-tui/src/slashRpc.ts`
- `ui-tui/src/slashDisplay.ts`
- `docs/cli.md`
- `docs/tui.md`
- `docs/troubleshooting.md`
- `README.md`
- `tests/test_migration_mirror_sync.py`
- `tests/test_supervisor_commander.py`
- `tests/test_cli_prompt.py`
- `tests/test_tui_pty.py`

---

## 4. Data Model

Migration `021_project_brain.sql` must be byte-identical in both migration roots.

```sql
CREATE TABLE IF NOT EXISTS project_brain_snapshots (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    repo_path TEXT NOT NULL,
    git_head TEXT NOT NULL DEFAULT '',
    git_dirty INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ready',
    summary TEXT NOT NULL DEFAULT '',
    file_count INTEGER NOT NULL DEFAULT 0,
    indexed_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_project_brain_snapshots_project
ON project_brain_snapshots(project_id, updated_at);

CREATE TABLE IF NOT EXISTS project_brain_cards (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    card_type TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    data_json TEXT NOT NULL DEFAULT '{}',
    citations_json TEXT NOT NULL DEFAULT '[]',
    confidence REAL NOT NULL DEFAULT 0.5,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_project_brain_cards_lookup
ON project_brain_cards(project_id, card_type, title);

CREATE TABLE IF NOT EXISTS project_context_packets (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    task_id TEXT,
    goal_id INTEGER,
    purpose TEXT NOT NULL,
    token_budget INTEGER NOT NULL,
    packet_json TEXT NOT NULL,
    redaction_report_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_project_context_packets_project
ON project_context_packets(project_id, created_at);

CREATE TABLE IF NOT EXISTS project_brain_memories (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    data_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_project_brain_memories_dedupe
ON project_brain_memories(project_id, source_type, source_id, memory_type, title);
```

Allowed enum values:

- `project_brain_cards.card_type`: `overview`, `component`, `test`, `command`, `config`, `entrypoint`, `migration`, `hazard`, `workflow`
- `project_brain_memories.memory_type`: `failure`, `success`, `review_blocker`, `hazard`, `decision`, `verification`
- `project_context_packets.purpose`: `commander_chat`, `task_prompt`, `review`, `impact`, `user_query`

---

## 5. Task Table

| Task | Owner | Commit Message | Scope | Tests |
| --- | --- | --- | --- | --- |
| 0 | Grok | `test: capture project brain contracts` | Red tests for indexing, redaction, context packet size, memory persistence, RPCs, and slash rendering. | Phase 11 focused tests fail for missing implementation only. |
| 1 | Grok | `feat: persist project brain snapshots` | Migration 021, mirrored migrations, snapshot/card/memory/packet helpers. | `tests.test_project_brain tests.test_migration_mirror_sync` |
| 2 | Grok | `feat: index repository structure safely` | `project_indexer.py` scans allowed repo files, ignores secrets/build/vendor dirs, records git head and dirty state. | `tests.test_project_indexer` |
| 3 | Grok | `feat: generate project brain cards` | Build overview/component/test/command/config cards with citations and confidence. | `tests.test_project_brain tests.test_project_indexer` |
| 4 | Grok | `feat: build bounded context packets` | `context_packets.py` selects relevant cards/files/history under token/byte budget with redaction report. | `tests.test_context_packets tests.test_cli_file_context` |
| 5 | Grok | `feat: attach brain context to Commander` | Feed `commander_chat` packets into Commander prompts without raw secret leakage or uncontrolled prompt growth. | `tests.test_supervisor_commander tests.test_context_packets` |
| 6 | Grok | `feat: attach task context to worker prompts` | Worker task prompts include `task_prompt` packet path and summary; packet is persisted for audit. | `tests.test_commander_runner tests.test_context_packets` |
| 7 | Grok | `feat: learn durable memories from task outcomes` | Convert failed tasks, review blockers, verification evidence, and successful fixes into memories. | `tests.test_project_brain tests.test_phase11_project_brain_e2e` |
| 8 | Grok | `feat: expose project brain RPCs` | Add `project.brain`, `project.map`, `project.where`, `project.why`, `project.impact`, `project.context`. | `tests.test_supervisor_methods tests.test_cli_prompt` |
| 9 | Grok | `feat: add project brain slash commands` | Add `/brain`, `/map`, `/where`, `/why`, `/impact`, `/context` to CLI/TUI. | `tests.test_cli_prompt tests.test_tui_pty`, `npm test --prefix ui-tui -- --run` |
| 10 | Gemini | `docs: record Phase 11 adversarial review` | Read-only review. No production edits. | Gemini checklist below. |
| 11 | Grok | `docs: document project brain context engine` | Update README, CLI/TUI/troubleshooting docs, acceptance handoff. | Full Gate G commands. |

---

## 6. Required Behaviors

### 6.1 Indexing

Indexer must:

- reject paths outside the registered project root;
- ignore `.git`, `.env`, `node_modules`, `.venv`, `dist`, `build`, caches, binary files, large logs, and files over the configured byte cap;
- capture citations as relative paths plus optional line spans;
- never store raw secret-looking values;
- compute git head and dirty state without requiring network;
- be idempotent for unchanged repositories.

### 6.2 Context Packets

Every context packet must include:

```json
{
  "project_id": "proj-...",
  "purpose": "task_prompt",
  "token_budget": 4000,
  "summary": "Short project/task-specific context",
  "cards": [],
  "citations": [],
  "memories": [],
  "redactions": {"count": 0, "patterns": []}
}
```

Packets must fail closed if selected context exceeds budget after compression. The failure should be returned as a structured error, not silently ignored.

### 6.3 User Commands

Expected examples:

```bash
coordinator --print -p "/brain"
coordinator --print -p "/map"
coordinator --print -p "/where add GitHub retry policy"
coordinator --print -p "/why src/local_cli_coordinator/supervisor_methods.py"
coordinator --print -p "/impact src/local_cli_coordinator/db.py"
coordinator --print -p "/context task-abc123"
```

`/where` and `/impact` may be heuristic. They must clearly label uncertain results and cite why each file was selected.

---

## 7. Gate Schedule

### Gate A — Red-Test Quality

Owner: Codex after Task 0.

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_project_brain \
  tests.test_project_indexer \
  tests.test_context_packets \
  tests.test_impact_analysis \
  tests.test_phase11_project_brain_e2e -v
```

Reject if tests pass before implementation, index real user secrets, require network, or assert only superficial strings.

### Gate B — Indexing and Secret Safety

Owner: Gemini after Task 3.

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_project_brain \
  tests.test_project_indexer \
  tests.test_migration_mirror_sync -v
```

Reject if `.env`, private keys, auth tokens, raw logs, build artifacts, or cross-project paths appear in cards.

### Gate C — Context Packet Integration

Owner: Codex after Task 6.

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_context_packets \
  tests.test_supervisor_commander \
  tests.test_commander_runner \
  tests.test_cli_file_context -v
```

Reject if Commander/worker prompts include unredacted context files, exceed budget, or fail to persist the packet used.

### Gate D — Memory and Staleness

Owner: Gemini after Task 7.

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_project_brain \
  tests.test_phase11_project_brain_e2e -v
```

Reject if stale memories override current repo state, duplicate memories grow unbounded, or resolved failures keep being presented as active blockers.

### Gate E — RPC and TUI Surface

Owner: Codex after Task 9.

```bash
PYTHONPATH=src python3 -m unittest tests.test_supervisor_methods tests.test_cli_prompt tests.test_tui_pty -v
npm run typecheck --prefix ui-tui
npm run lint --prefix ui-tui
npm test --prefix ui-tui -- --run
```

Reject if `/brain`, `/map`, `/where`, `/why`, `/impact`, or `/context` bypass project scoping or show raw prompt/log bodies.

### Gate F — Gemini Final Adversarial Review

Owner: Gemini after Task 11.

Checklist:

- project A cannot read project B brain data;
- fake secrets are redacted in snapshots, cards, packets, CLI, TUI, and logs;
- dirty git state invalidates stale cards or clearly marks them stale;
- context packets are bounded and deterministic enough for tests;
- worker prompts cite packet IDs;
- docs match actual CLI/TUI behavior.

### Gate G — Codex Final Sign-Off

Owner: Codex after Gemini PASS.

```bash
git diff --check
PYTHONWARNINGS=error::ResourceWarning PYTHONPATH=src python3 -m unittest discover -s tests -q
npm run typecheck --prefix ui-tui
npm run lint --prefix ui-tui
npm test --prefix ui-tui -- --run
npm run build --prefix ui-tui
PYTHONPATH=src python3 -m unittest tests.test_tui_bundle tests.test_wheel_migrations -v
python3 -m build
```

Clean-wheel smoke must install the built wheel without `PYTHONPATH` and run:

```bash
coordinator init --dry-run --json
coordinator init --yes --json
coordinator doctor --json
coordinator --print -p "/brain"
coordinator --print -p "/map"
```

---

## 8. Execution Instructions for Grok

1. Start from the branch that contains Phase 10.
2. Create branch `phase11-project-brain-context-engine`.
3. Use one commit per task.
4. Stop at every Codex/Gemini gate.
5. Do not implement Phase 12 or Phase 13 in this branch.
6. Do not call network services or live LLM providers in tests.
7. Record final evidence in `docs/superpowers/handoffs/2026-06-30-phase11-acceptance.md`.
