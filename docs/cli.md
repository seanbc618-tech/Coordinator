# Coordinator CLI Prompt Modes

Phase 5.3 adds Pi-inspired headless entry points on top of the global Supervisor
`chat.send` path. The Ink TUI remains the default interactive shell.

## Prompt and print

```bash
# Send a message, then open the TUI (default)
coordinator "检查这个项目状态"

# Print reply without opening Ink
coordinator --print -p "你好"

# Positional prompt with print
coordinator 检查项目状态 --print
```

## JSON output

```bash
coordinator --print --mode json -p "现在有什么任务？"
```

Stdout is a single JSON object with keys: `ok`, `project_id`, `goal_id`,
`user_reply`, `intent`, `admitted`, `rejected`, `accepted_task_ids`, `error`.

## Continue latest goal

```bash
coordinator --continue --print -p "下一步做什么？"
```

Binds to the newest non-terminal goal for the current git project.

## Local slash commands (print mode)

Leading `/` dispatches deterministic RPCs without Commander:

```bash
coordinator --print -p "/status"
coordinator --print -p "/tasks"
```

Unknown slash commands return a local error and never call `chat.send`.

## Skip TUI after prompt

```bash
coordinator --no-tui -p "记录一下进度"
coordinator --print -p "总结状态"   # --print implies --no-tui
```

## Config inspection

```bash
coordinator config
```

Shows agents, repo allowlist, policy caps, and XDG/runtime paths. Read-only in
Phase 5.3.

## Examples (polymarket)

```bash
cd /Users/xiafan/polymarket-crypto-threshold
coordinator supervisor restart
coordinator --print -p "你好"
coordinator --mode json --print -p "现在有什么任务？"
coordinator --continue --print -p "生成一个只读验收任务"
coordinator config
coordinator "打开 TUI 继续"
```

Greetings and status questions should not create tasks. Only explicit task
requests may admit work after Commander policy checks.

## File context

Attach repo-relative files to the prompt with `@` tokens:

```bash
coordinator @README.md -p "检查文档中的安装步骤"
coordinator @docs/tui.md @docs/cli.md --print -p "找出矛盾"
coordinator --mode json @pyproject.toml -p "总结配置"
```

Rules:

- References are resolved from the current working directory, not the git root.
- Each file must exist, be a regular file, decode as UTF-8, and be ≤ 128 KiB.
- Combined limit: 512 KiB and at most 16 files.
- Duplicate canonical paths are included once.
- `@@name` escapes the syntax and becomes the literal token `@name`.

JSON mode adds a `context_files` array with `path`, `size`, and `sha256` for
each attached file.

## Goal sessions

```bash
# Resume the latest resumable goal (interactive selector in TTY)
coordinator --resume

# Resume a specific goal by ID
coordinator --resume 42 -p "继续分析"

# Fork a terminal goal into a new draft
coordinator --fork 17 -p "只保留文档修复部分"
```

Rules:

- `--continue`, `--resume`, and `--fork` are mutually exclusive.
- `--resume` without an ID lists candidates (exit 2 in non-interactive mode).
- Fork creates a new draft goal; it does not copy tasks or execution history.
- Cross-project resume/fork is rejected.

## Execution tool controls

Restrict which execution stages Commander may use for this request:

```bash
# Conversation only — no task proposals admitted
coordinator --no-tools -p "解释当前状态"

# Allow only specific stages (aliases: grep→search, write→edit)
coordinator --tools read,grep -p "只读检查风险"

# Exclude specific stages from the server policy
coordinator --exclude-tools push,merge -p "修复但不要发布"
```

Vocabulary: `read`, `search`, `test`, `edit`, `commit`, `push`, `merge`.

Precedence:

- `--tools` and `--no-tools` are mutually exclusive.
- `--tools` and `--exclude-tools` may be combined; exclusion wins.
- Restrictions never enable stages that server-side repo policy forbids.

JSON mode includes the effective `execution_policy` in the output.

## RPC mode

```bash
coordinator --mode rpc -p "/status"
```

RPC mode is headless and prints one JSON-encoded Supervisor protocol
`ResponseEnvelope`. It is intentionally protocol-level and versioned by
`protocol_version`. JSON mode remains the smaller stable public CLI schema.

Errors also produce a valid `ResponseEnvelope` with `ok: false` and
`request_id` prefixed `cli-local-`.