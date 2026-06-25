# Phase 5.4 Context, Sessions, and Tool Controls Design

Date: 2026-06-26
Branch: `external/coordinator-global-tui`
Baseline: `b6b729d`

## Goal

Complete the remaining Pi-inspired CLI backlog in three sequential waves:

1. bounded `@file` context;
2. project-scoped goal resume and fork;
3. temporary execution restrictions and raw RPC output.

The features share the Phase 5.3 headless CLI, but they must remain separate
internally. File context describes input, session operators select durable goal
state, and tool controls restrict execution. No flag may bypass repository
allowlists, budgets, review gates, or merge policy.

## Non-Goals

- Do not import Pi source code.
- Do not replace the Hermes/Ink TUI.
- Do not add a second database or background service.
- Do not claim OS-level sandboxing for tool flags.
- Do not store complete referenced file contents in SQLite.
- Do not resurrect completed goals in place.
- Do not let `--resume`, `--fork`, or tool flags act across projects.

## Wave A: Bounded File Context

### CLI

Examples:

```bash
coordinator @README.md -p "检查文档中的安装步骤"
coordinator @docs/tui.md @docs/cli.md --print -p "找出矛盾"
coordinator --mode json @pyproject.toml -p "总结配置"
```

Any positional token beginning with `@` is a file reference. `@@name` escapes
the syntax and becomes the literal prompt token `@name`.

### Resolution

References are resolved from the operator's current working directory, not the
git root. Each resolved file must:

- exist and be a regular file;
- resolve inside the current registered repository after following symlinks;
- not traverse outside the repository through `..`, symlink aliases, or macOS
  case normalization;
- decode as UTF-8 without NUL bytes;
- be at most 128 KiB.

The combined decoded content limit is 512 KiB and at most 16 files. Duplicate
canonical paths are included once.

### Trust Boundary

The CLI sends only repo-relative references through `chat.send`:

```json
{
  "context_files": [
    {"path": "docs/cli.md"}
  ]
}
```

The Supervisor resolves and reads the files again from the registered project's
canonical root. This prevents another local client from bypassing CLI checks.
The Supervisor produces a context manifest containing:

```json
{
  "path": "docs/cli.md",
  "size": 4096,
  "sha256": "...",
  "content_type": "text/plain"
}
```

Only the manifest is persisted. File contents are placed in the Commander prompt
only while the process is running. When the run ends, Coordinator rewrites the
prompt artifact to a metadata-only redacted form containing path, size, and
hash. The normal user message stores the prompt text plus a list of referenced
paths, not the file bodies.

### Output

Text mode prints the normal `user_reply`. JSON adds:

```json
{
  "context_files": [
    {"path": "docs/cli.md", "size": 4096, "sha256": "..."}
  ]
}
```

Errors use stable codes such as `context_outside_repo`, `context_too_large`,
`context_binary`, and `context_missing`.

## Wave B: Resume and Fork

Coordinator goals are the durable session model. No separate conversation
session table is introduced.

### Resume

```bash
coordinator --resume 42 -p "继续分析"
coordinator --resume --mode json
```

Rules:

- Goal IDs are valid only when `goal.project_id` matches the current project.
- Active goals are selected without mutation.
- Paused or blocked goals may be resumed to `active`.
- Draft goals are selected but still require `/goal confirm` before chat.
- Completed, failed, and abandoned goals cannot be resumed. The error recommends
  `--fork`.
- If another non-terminal goal exists, resuming a different goal is rejected.

`--resume` without an ID lists project goal candidates:

- in a TTY text session, show a numbered selector and require confirmation;
- in non-interactive text mode, print a table and exit `2`;
- in JSON/RPC mode, return candidate records and exit `2`.

Candidates include ID, title, status, updated time, progress summary, linked
task counts, and source/fork lineage.

### Fork

```bash
coordinator --fork 17 -p "只保留文档修复部分"
```

Fork creates a new **draft** goal for the current project. It copies:

- source objective;
- completion criteria;
- constraints;
- repo IDs;
- latest progress summary;
- a bounded summary of recent Commander messages and linked task outcomes.

The supplied prompt becomes an additional fork instruction. Fork does not copy
tasks, leases, attempts, artifacts, or Commander runs. It does not invoke
Commander and does not automatically confirm or activate the new goal.

Fork is rejected while any non-terminal goal already exists for the project.
Migration 012 adds nullable `goals.parent_goal_id` and an index. Lineage is
project-scoped and immutable.

### CLI Exclusivity

`--continue`, `--resume`, and `--fork` are mutually exclusive.

## Wave C: Temporary Tool Controls and RPC Mode

### Execution Tool Vocabulary

Coordinator exposes these restriction names:

```text
read, search, test, edit, commit, push, merge
```

Aliases accepted at the CLI:

```text
grep -> search
write -> edit
```

Examples:

```bash
coordinator --no-tools -p "解释当前状态"
coordinator --tools read,grep -p "只读检查风险"
coordinator --exclude-tools push,merge -p "修复但不要发布"
```

### Semantics

- No flags: use the normal repository and global policy.
- `--no-tools`: Commander conversation only. Any task proposal is rejected.
- `--tools X`: intersect the requested set with policy-available stages.
- `--exclude-tools X`: remove stages from the policy-available set.
- `--tools` and `--no-tools` are mutually exclusive.
- `--tools` and `--exclude-tools` may be combined; exclusion wins.
- Unknown names are errors.
- Restrictions never enable push or merge when repo policy forbids them.

The server derives policy-available stages as follows:

- `read`, `search`, `edit`, and `commit` are available for an allowlisted repo;
- `test` is available when the repo has verification commands;
- `push` requires `allow_push = true` and a merge policy other than `no_push`;
- `merge` requires an auto-merge repository policy.

### Enforcement

Tool controls are Coordinator execution-stage restrictions, not an OS shell
sandbox. They are enforced at multiple boundaries:

| Stage | Enforcement |
|---|---|
| Commander | Prompt states the effective restriction and permitted task shape |
| Admission | Proposals needing a forbidden stage are rejected |
| Worker | Prompt includes the effective policy |
| Post-worker | If `edit` is forbidden, any worktree change fails the attempt |
| Verification | Repository verification requires `test` |
| Commit | Commit stage requires `commit` |
| Push | Push stage requires `push` and existing repo policy |
| Merge | Merge stage requires `merge` and existing repo policy |

Read/search-only tasks must have `expected_files = 0` and use capabilities that
an eligible worker supports. Because a worker CLI can technically execute shell
commands, Coordinator verifies observable outcomes: a no-edit task that changes
the worktree is failed and never committed or pushed.

If `edit` and `test` are allowed but `commit` is forbidden, a successful worker
attempt transitions to `awaiting_human`, preserves its isolated worktree, and
reports the changed files. It does not enter `done`.

### Persistence

Migration 013 adds:

```sql
alter table commander_runs add column context_manifest text not null default '[]';
alter table commander_runs add column execution_policy text not null default '{}';
alter table tasks add column execution_policy text not null default '{}';
```

The effective policy, not merely the requested flags, is persisted. Admitted
tasks inherit the Commander run's effective policy, so daemon execution after
CLI exit preserves the restriction.

### RPC Mode

```bash
coordinator --mode rpc -p "/status"
```

RPC mode is headless and prints one JSON-encoded Supervisor protocol
`ResponseEnvelope`. For local slash commands, Coordinator returns the actual
Supervisor response envelope. For prompt chat, it returns the `chat.send`
response unchanged.

RPC mode is intentionally protocol-level and versioned by
`protocol_version`. JSON mode remains the smaller stable public CLI schema.

Errors in RPC mode also use a valid `ResponseEnvelope`, with `ok: false`.

## Data Flow

```text
CLI argv
  -> parse @files / session selector / requested restrictions
  -> resolve registered project
  -> select or fork project goal
  -> compute policy intersection
  -> chat.send {
       text,
       goal_id,
       context_files,
       execution_policy
     }
  -> Supervisor revalidates project, files, goal, and policy
  -> Commander prompt receives ephemeral file content + effective policy
  -> admission persists effective policy on accepted tasks
  -> engine enforces restricted stages
  -> text/json/rpc result
```

## API Changes

`chat.send` accepts optional:

```json
{
  "goal_id": 42,
  "context_files": [{"path": "README.md"}],
  "execution_policy": {
    "allowed": ["read", "search", "test"],
    "source": "cli"
  }
}
```

The Supervisor ignores client-supplied hashes, sizes, absolute paths, and
policy claims that exceed server policy. It returns the validated context
manifest and effective execution policy.

## Error Handling

All new headless errors have stable codes in JSON mode and readable stderr in
text mode. Expected codes include:

- `context_missing`
- `context_outside_repo`
- `context_binary`
- `context_too_large`
- `goal_not_found`
- `goal_wrong_project`
- `goal_not_resumable`
- `goal_conflict`
- `fork_conflict`
- `tool_unknown`
- `tool_conflict`
- `tool_policy_rejected`

No validation failure starts Commander or admits a task.

## Delivery Waves

### Wave A

Ship file parsing, double validation, context manifests, prompt integration,
JSON output, and adversarial path tests.

### Wave B

Ship goal listing, resume rules, fork lineage migration, selectors, and
project-isolation tests.

### Wave C

Ship execution policy parsing, persistence, admission/engine enforcement, RPC
mode, documentation, and final integrated smoke tests.

Each wave must be independently usable and green before the next begins.

## Role Assignment

- **Grok:** production implementation for all three waves, one bounded commit
  per task.
- **Claude Code:** red tests, fixture generation, documentation, and gate output
  collection. Claude must not implement path security, goal transitions,
  policy intersection, or engine enforcement.
- **Codex:** Wave A/B/C gates and final clean-wheel acceptance.

## Acceptance Criteria

### Files

- Repo-relative UTF-8 files reach Commander and appear in the run manifest.
- Absolute paths, symlink escape, `..` escape, binary files, too many files,
  oversized files, and aggregate overflow fail before Commander runs.
- SQLite contains metadata only, not referenced file content.

### Sessions

- A paused project goal can be resumed.
- A terminal goal cannot be resumed and can be forked into a new draft.
- Cross-project resume/fork is rejected.
- Fork copies bounded context but no tasks or execution history.
- Only one non-terminal goal per project remains enforced.

### Tools

- `--no-tools` admits zero tasks.
- Read/search-only policy fails any attempt that changes files.
- A no-commit task can edit and verify but cannot commit.
- Push/merge remain impossible when repo policy forbids them.
- Restrictions survive CLI exit and daemon execution.

### Output and Regression

- JSON remains stable and headless.
- RPC emits one valid protocol envelope and is headless.
- Existing prompt, TUI, Supervisor, PTY/E2E, wheel, ResourceWarning, and full
  Python gates remain green.

## Deferred

- File glob expansion.
- Directory attachments.
- Binary/image attachments.
- Editing config through TUI.
- OS-level command sandboxing.
- Cross-project goal cloning.
- Remote clients or network RPC.
