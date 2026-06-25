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