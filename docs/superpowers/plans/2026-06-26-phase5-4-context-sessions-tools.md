# Phase 5.4 Context, Sessions, and Tool Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add safe `@file` context, project-scoped `--resume`/`--fork`, persistent execution restrictions, and raw RPC output to the Phase 5.3 headless CLI.

**Architecture:** Implement three sequential waves over the existing global Supervisor path. The CLI parses operator intent, while the Supervisor independently revalidates project files, goal ownership, and effective policy. Restrictions are persisted on Commander runs and tasks so later daemon execution cannot lose them.

**Tech Stack:** Python 3.13, argparse, SQLite migrations, Unix socket RPC, existing Commander/Supervisor/engine stack, `unittest`.

**Design:** `docs/superpowers/specs/2026-06-26-phase5-4-context-sessions-tools-design.md`

---

## Ownership and Merge Order

- **Claude Code:** Tasks 0, 4, 7, and 10 only. Create new tests/docs; do not edit production code.
- **Grok:** Tasks 1–3, 5–6, 8–9, and 11. Production implementation and integration.
- **Codex:** Gate A after Task 3, Gate B after Task 6, Gate C/final after Task 11.

Execution order is strict:

```text
Claude 0 -> Grok 1 -> Grok 2 -> Grok 3 -> Codex Gate A
Claude 4 -> Grok 5 -> Grok 6 -> Codex Gate B
Claude 7 -> Grok 8 -> Grok 9 -> Claude 10 -> Grok 11 -> Codex Gate C
```

## File Map

New focused modules:

- `src/local_cli_coordinator/context_files.py`: parsing, canonical validation,
  bounded reads, manifest generation, prompt rendering/redaction.
- `src/local_cli_coordinator/goal_sessions.py`: candidate listing, resume rules,
  fork creation and lineage summaries.
- `src/local_cli_coordinator/execution_policy.py`: vocabulary, aliases, policy
  intersection, JSON serialization, stage checks.

New tests owned by Claude:

- `tests/test_cli_file_context.py`
- `tests/test_goal_sessions.py`
- `tests/test_execution_policy.py`
- `tests/test_phase5_4_e2e.py`

Migrations must be byte-identical in:

- `src/local_cli_coordinator/migrations/012_goal_lineage.sql`
- `migrations/012_goal_lineage.sql`
- `src/local_cli_coordinator/migrations/013_execution_context.sql`
- `migrations/013_execution_context.sql`

## Wave A: File Context

### Task 0: File Context Red Tests

**Owner:** Claude Code

**Files:**
- Create: `tests/test_cli_file_context.py`

- [ ] Add parser tests using `build_prompt_parser()` and
  `normalize_prompt_args()`:

```python
def test_file_tokens_are_separated_from_prompt(self):
    args = self._parse(["@README.md", "@docs/cli.md", "-p", "compare"])
    self.assertEqual(args.context_file_tokens, ["README.md", "docs/cli.md"])
    self.assertEqual(args.prompt_text, "compare")

def test_double_at_escapes_literal_prompt(self):
    args = self._parse(["@@owner", "-p", "notify"])
    self.assertEqual(args.context_file_tokens, [])
    self.assertEqual(args.prompt_text, "@owner notify")
```

- [ ] Add unit fixtures for one valid UTF-8 file, duplicate canonical paths,
  missing file, directory, NUL/binary file, 128 KiB overflow, 17 files, 512 KiB
  aggregate overflow, `../` escape, and symlink escape.
- [ ] Add a Supervisor-boundary test that sends an unchecked
  `context_files=[{"path": "../outside.txt"}]` directly and expects rejection
  before Commander invocation.
- [ ] Add persistence assertions:

```python
self.assertNotIn(secret_body, message["content"])
self.assertNotIn(secret_body, run["context_manifest"])
self.assertIn("sha256", json.loads(run["context_manifest"])[0])
```

- [ ] Add JSON output assertion for `context_files`.
- [ ] Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_cli_file_context -v
```

Expected before implementation: import/attribute failures for
`context_files`, `context_file_tokens`, and context manifest handling.

- [ ] Commit:

```bash
git add tests/test_cli_file_context.py
git commit -m "test: capture Phase 5.4 file context requirements"
```

### Task 1: CLI File Token Parsing

**Owner:** Grok

**Files:**
- Modify: `src/local_cli_coordinator/cli.py`
- Modify: `src/local_cli_coordinator/cli_chat.py`
- Test: `tests/test_cli_file_context.py`

- [ ] Change `normalize_prompt_args()` to partition positional words:

```python
context_tokens: list[str] = []
prompt_parts: list[str] = []
for word in args.prompt_words:
    if word.startswith("@@"):
        prompt_parts.append(word[1:])
    elif word.startswith("@") and len(word) > 1:
        context_tokens.append(word[1:])
    else:
        prompt_parts.append(word)
args.context_file_tokens = context_tokens
```

The explicit `-p/--prompt` string remains prompt text and is never parsed for
`@file`.

- [ ] Add `context_files` to `PromptOutcome.to_json_dict()`.
- [ ] Keep `--mode json` headless.
- [ ] Run parser-focused tests and existing `tests.test_cli_prompt`.
- [ ] Commit:

```bash
git add src/local_cli_coordinator/cli.py src/local_cli_coordinator/cli_chat.py
git commit -m "feat: parse bounded file references in CLI prompts"
```

### Task 2: Double-Validated Context Files

**Owner:** Grok

**Files:**
- Create: `src/local_cli_coordinator/context_files.py`
- Modify: `src/local_cli_coordinator/cli_chat.py`
- Modify: `src/local_cli_coordinator/supervisor_commander.py`
- Modify: `src/local_cli_coordinator/commander_service.py`
- Test: `tests/test_cli_file_context.py`

- [ ] Define:

```python
MAX_CONTEXT_FILES = 16
MAX_CONTEXT_FILE_BYTES = 128 * 1024
MAX_CONTEXT_TOTAL_BYTES = 512 * 1024

@dataclass(frozen=True)
class ContextFile:
    path: str
    size: int
    sha256: str
    content: str

def load_context_files(repo_root: Path, cwd: Path, tokens: list[str]) -> list[ContextFile]:
    ...
```

- [ ] Resolve each token against `cwd`, call `.resolve(strict=True)`, require
  `candidate.is_relative_to(repo_root.resolve())`, require `is_file()`, reject
  NUL and decode failures, then enforce individual and aggregate byte limits.
- [ ] Deduplicate by resolved canonical path while preserving first-seen order.
- [ ] CLI sends only `{"path": repo_relative_posix}`.
- [ ] Supervisor looks up the registered project canonical path and calls the
  same loader with `cwd=repo_root`; it ignores client sizes/hashes.
- [ ] Pass validated context objects to `send_project_chat_message()`.
- [ ] Return only metadata in `chat.send` and `PromptOutcome`.
- [ ] Map `ContextFileError.code` to the stable design codes.
- [ ] Run all `test_cli_file_context` tests.
- [ ] Commit:

```bash
git add src/local_cli_coordinator/context_files.py \
  src/local_cli_coordinator/cli_chat.py \
  src/local_cli_coordinator/supervisor_commander.py \
  src/local_cli_coordinator/commander_service.py
git commit -m "feat: validate project file context at CLI and Supervisor"
```

### Task 3: Commander Context Manifest and Redaction

**Owner:** Grok

**Files:**
- Create: `src/local_cli_coordinator/migrations/013_execution_context.sql`
- Create: `migrations/013_execution_context.sql`
- Modify: `src/local_cli_coordinator/goals.py`
- Modify: `src/local_cli_coordinator/commander_runner.py`
- Modify: `src/local_cli_coordinator/commander_service.py`
- Test: `tests/test_cli_file_context.py`
- Test: `tests/test_migration_mirror_sync.py`

- [ ] Add migration 013 now with both context and policy columns:

```sql
alter table commander_runs add column context_manifest text not null default '[]';
alter table commander_runs add column execution_policy text not null default '{}';
alter table tasks add column execution_policy text not null default '{}';
```

- [ ] Extend `acquire_commander_run_slot()` to accept manifest/policy JSON and
  insert them atomically with the running row.
- [ ] Extend `run_commander()` with:

```python
context_files: list[ContextFile] | None = None
execution_policy: dict[str, object] | None = None
```

- [ ] Append file bodies under delimiters:

```text
## Operator file context
--- BEGIN FILE: docs/cli.md sha256=<hash> ---
<content>
--- END FILE: docs/cli.md ---
```

- [ ] In `_finish_commander_attempt()`, rewrite `prompt.md` to the normal
  Commander context plus a metadata-only manifest. Use `try/finally` so timeout,
  parse error, nonzero exit, and KeyboardInterrupt all redact.
- [ ] Persist Commander user message as:

```text
<operator text>

[context files: README.md, docs/cli.md]
```

- [ ] Run file-context, commander-runner, migration mirror, and wheel migration
  tests.
- [ ] Commit:

```bash
git add migrations/013_execution_context.sql \
  src/local_cli_coordinator/migrations/013_execution_context.sql \
  src/local_cli_coordinator/goals.py \
  src/local_cli_coordinator/commander_runner.py \
  src/local_cli_coordinator/commander_service.py
git commit -m "feat: persist file manifests and redact Commander prompts"
```

**Gate A:** Codex independently verifies path attacks, no persisted body, JSON
manifest, full Python suite, and clean wheel.

## Wave B: Goal Resume and Fork

### Task 4: Goal Session Red Tests

**Owner:** Claude Code

**Files:**
- Create: `tests/test_goal_sessions.py`

- [ ] Add tests for project-scoped candidate listing and exact ordering by
  `updated_at desc, id desc`.
- [ ] Add resume matrix:

```python
cases = {
    "active": "active",
    "paused": "active",
    "blocked": "active",
    "draft": "draft",
}
```

- [ ] Assert completed/failed/abandoned goals return `goal_not_resumable`.
- [ ] Assert cross-project IDs return `goal_wrong_project`.
- [ ] Assert an existing different non-terminal goal returns `goal_conflict`.
- [ ] Add fork tests: terminal source creates a new draft, copies bounded
  objective/criteria/constraints/repos/progress, sets `parent_goal_id`, copies
  no task links/runs/attempts, and does not invoke Commander.
- [ ] Add parser mutual-exclusion tests for `--continue`, `--resume`, `--fork`.
- [ ] Add no-ID candidate output tests for text, JSON, RPC, and noninteractive
  exit `2`.
- [ ] Commit:

```bash
git add tests/test_goal_sessions.py
git commit -m "test: capture Phase 5.4 goal session requirements"
```

### Task 5: Goal Lineage and Session Service

**Owner:** Grok

**Files:**
- Create: `src/local_cli_coordinator/migrations/012_goal_lineage.sql`
- Create: `migrations/012_goal_lineage.sql`
- Create: `src/local_cli_coordinator/goal_sessions.py`
- Modify: `src/local_cli_coordinator/goals.py`
- Test: `tests/test_goal_sessions.py`

- [ ] Add migration:

```sql
alter table goals add column parent_goal_id integer references goals(id);
create index if not exists idx_goals_parent_goal on goals(parent_goal_id);
```

- [ ] Add `list_project_goal_candidates(conn, project_id)` with linked task
  counts.
- [ ] Add:

```python
def resume_project_goal(conn, project_id: str, goal_id: int) -> sqlite3.Row:
    ...

def fork_project_goal(
    conn, project_id: str, source_goal_id: int, instruction: str
) -> int:
    ...
```

- [ ] For paused/blocked resume, clear Commander failures and transition active.
  Draft remains draft. Reject terminal and conflicting goals.
- [ ] Bound fork message summary to five messages, 500 chars each, and linked
  task outcome summary to twenty tasks.
- [ ] Build the fork objective from source objective plus:

```text
Fork instruction: <instruction>
Source progress: <bounded progress>
```

- [ ] Run goal session, goals, and migration tests.
- [ ] Commit:

```bash
git add migrations/012_goal_lineage.sql \
  src/local_cli_coordinator/migrations/012_goal_lineage.sql \
  src/local_cli_coordinator/goal_sessions.py \
  src/local_cli_coordinator/goals.py
git commit -m "feat: add project goal resume and fork lineage"
```

### Task 6: CLI Session Operators

**Owner:** Grok

**Files:**
- Modify: `src/local_cli_coordinator/cli.py`
- Modify: `src/local_cli_coordinator/cli_chat.py`
- Modify: `src/local_cli_coordinator/supervisor_methods.py`
- Test: `tests/test_goal_sessions.py`

- [ ] Add mutually exclusive parser options:

```python
session = parser.add_mutually_exclusive_group()
session.add_argument("--continue", dest="continue_goal", action="store_true")
session.add_argument("--resume", nargs="?", const="", metavar="GOAL_ID")
session.add_argument("--fork", type=int, metavar="GOAL_ID")
```

- [ ] Add Supervisor methods `project.goals`, `project.goal.resume`, and
  `project.goal.fork`; all require request project scope.
- [ ] CLI uses RPC for mutation, not direct database writes.
- [ ] No-ID noninteractive resume emits candidates and exit `2`; TTY selector
  accepts only an integer shown in the list and asks `Resume goal N? [y/N]`.
- [ ] Fork prints the new draft ID and instruction to run `/goal confirm`.
- [ ] Add lineage/candidate fields to JSON public output.
- [ ] Run goal sessions, Supervisor methods, CLI prompt, and PTY regression.
- [ ] Commit:

```bash
git add src/local_cli_coordinator/cli.py \
  src/local_cli_coordinator/cli_chat.py \
  src/local_cli_coordinator/supervisor_methods.py
git commit -m "feat: expose project goal resume and fork in CLI"
```

**Gate B:** Codex verifies state matrix, project isolation, no history copying,
candidate behavior, migrations, and full suite.

## Wave C: Execution Restrictions and RPC

### Task 7: Execution Policy Red Tests

**Owner:** Claude Code

**Files:**
- Create: `tests/test_execution_policy.py`

- [ ] Add parser tests for aliases, unknown tools, empty lists, conflicts, and
  exclusion precedence.
- [ ] Add policy intersection tests for repos with no-push, push-only, and
  auto-merge settings.
- [ ] Add admission tests:
  `--no-tools` rejects all proposals; read/search requires `expected_files=0`;
  no-test rejects proposals with verification commands.
- [ ] Add engine tests:
  no-edit plus changed worktree fails; no-test never invokes verification;
  no-commit preserves worktree and becomes `awaiting_human`; no-push/no-merge
  skip those Git operations.
- [ ] Assert task `execution_policy` survives closing the CLI database
  connection and a later daemon cycle.
- [ ] Add RPC mode envelope tests for `/status`, chat success, and errors.
- [ ] Commit:

```bash
git add tests/test_execution_policy.py
git commit -m "test: capture Phase 5.4 execution policy and RPC requirements"
```

### Task 8: Policy Parsing, Intersection, and Admission

**Owner:** Grok

**Files:**
- Create: `src/local_cli_coordinator/execution_policy.py`
- Modify: `src/local_cli_coordinator/cli.py`
- Modify: `src/local_cli_coordinator/cli_chat.py`
- Modify: `src/local_cli_coordinator/supervisor_commander.py`
- Modify: `src/local_cli_coordinator/commander_runner.py`
- Modify: `src/local_cli_coordinator/commander_policy.py`
- Modify: `src/local_cli_coordinator/commander_service.py`
- Test: `tests/test_execution_policy.py`

- [ ] Define canonical vocabulary and immutable model:

```python
TOOLS = frozenset({"read", "search", "test", "edit", "commit", "push", "merge"})
ALIASES = {"grep": "search", "write": "edit"}

@dataclass(frozen=True)
class ExecutionPolicy:
    allowed: frozenset[str]
    source: str = "default"
```

- [ ] Derive server policy from repo config exactly as the design specifies.
- [ ] Parse client restrictions, canonicalize aliases, intersect server-side,
  and serialize sorted arrays.
- [ ] Add effective policy to Commander prompt context.
- [ ] Reject response tasks when:
  `allowed` is empty; expected files require edit; verification requires test;
  or downstream mandatory stages cannot be satisfied.
- [ ] Pass effective policy into `admit_commander_response()` and persist it in
  `tasks.execution_policy`.
- [ ] Run policy, Commander policy, chat, and concurrency tests.
- [ ] Commit:

```bash
git add src/local_cli_coordinator/execution_policy.py \
  src/local_cli_coordinator/cli.py src/local_cli_coordinator/cli_chat.py \
  src/local_cli_coordinator/supervisor_commander.py \
  src/local_cli_coordinator/commander_runner.py \
  src/local_cli_coordinator/commander_policy.py \
  src/local_cli_coordinator/commander_service.py
git commit -m "feat: persist restrictive execution policies on Commander tasks"
```

### Task 9: Engine Stage Enforcement and RPC Mode

**Owner:** Grok

**Files:**
- Modify: `src/local_cli_coordinator/engine.py`
- Modify: `src/local_cli_coordinator/cli.py`
- Modify: `src/local_cli_coordinator/cli_chat.py`
- Modify: `src/local_cli_coordinator/supervisor_protocol.py`
- Test: `tests/test_execution_policy.py`

- [ ] Parse each task policy before worktree execution. Missing/empty JSON means
  legacy default policy.
- [ ] Before verification, require `test`; before commit/push/merge require each
  corresponding stage.
- [ ] If edit is forbidden and changed files exist, finish failed with
  `execution policy forbids edit`.
- [ ] If commit is forbidden after successful review, write the review packet,
  transition `awaiting_human`, preserve worktree, and return.
- [ ] Add `rpc` to `--mode`; `json` and `rpc` both imply `no_tui`.
- [ ] Refactor `_send_rpc()` to optionally return the original
  `ResponseEnvelope`. RPC output is `encode_envelope(response)` exactly once.
- [ ] Local validation errors construct a protocol-valid response with
  `request_id` prefixed `cli-local-`.
- [ ] Run execution policy, engine, protocol, CLI prompt, and PTY tests.
- [ ] Commit:

```bash
git add src/local_cli_coordinator/engine.py \
  src/local_cli_coordinator/cli.py \
  src/local_cli_coordinator/cli_chat.py \
  src/local_cli_coordinator/supervisor_protocol.py
git commit -m "feat: enforce task execution stages and add RPC output mode"
```

### Task 10: Documentation and Integrated Fixtures

**Owner:** Claude Code

**Files:**
- Create: `tests/test_phase5_4_e2e.py`
- Modify: `tests/fixtures/fake_supervisor.py`
- Modify: `tests/fixtures/fake_commander.py`
- Modify: `docs/cli.md`
- Modify: `docs/troubleshooting.md`
- Create: `docs/superpowers/handoffs/2026-06-26-phase5-4-acceptance.md`

- [ ] Add a three-wave subprocess test using a temp git repo and isolated
  `COORDINATOR_HOME`: attach a file, fork terminal goal, confirm draft, send
  read/search-only request, and inspect RPC output.
- [ ] Add fixture support for context manifest and execution policy fields.
- [ ] Document exact security limits, session state matrix, tool vocabulary,
  enforcement limitations, and RPC vs JSON.
- [ ] Record focused command output only; do not edit production code.
- [ ] Commit tests and docs separately:

```bash
git commit -m "test: add Phase 5.4 integrated CLI workflow"
git commit -m "docs: document context sessions tools and RPC mode"
```

### Task 11: Final Integration

**Owner:** Grok

- [ ] Integrate Claude Task 10 without weakening assertions.
- [ ] Run:

```bash
npm run typecheck --prefix ui-tui
npm run lint --prefix ui-tui
npm test --prefix ui-tui -- --run
PYTHONWARNINGS=error::ResourceWarning PYTHONPATH=src \
  python3 -m unittest discover -s tests -q
PYTHONPATH=src python3 -m unittest \
  tests.test_cli_file_context tests.test_goal_sessions \
  tests.test_execution_policy tests.test_phase5_4_e2e -v
PYTHONPATH=src python3 -m unittest \
  tests.test_tui_bundle.WheelPackagingTest tests.test_wheel_migrations -v
git diff --check
```

- [ ] Clean-wheel smoke from a registered temp repo:

```bash
coordinator @README.md --mode json -p "summarize"
coordinator --resume --mode json
coordinator --fork <terminal-goal-id> -p "docs only"
coordinator --no-tools --print -p "explain status"
coordinator --tools read,grep --mode rpc -p "/status"
coordinator --exclude-tools push,merge --print -p "make one small fix"
```

- [ ] Update acceptance handoff with exact counts, commit hashes, and known
  limitations.
- [ ] Commit:

```bash
git commit -m "docs: record Phase 5.4 integration gates"
```

## Final Acceptance

Codex must independently rerun:

- path traversal and redaction attacks;
- resume/fork state and project isolation;
- policy persistence and engine stage enforcement;
- JSON/RPC headless output;
- isolated XDG full suite;
- clean wheel build/install and temp-repo smoke.

No merge or Phase 5.5 planning before final Gate E PASS.
