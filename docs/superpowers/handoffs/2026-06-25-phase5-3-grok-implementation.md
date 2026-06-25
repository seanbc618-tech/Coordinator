# Grok Handoff: Phase 5.3 Main Implementation

You are the primary implementer.

Repository: `/Users/xiafan/Coordinator`
Branch: `external/coordinator-global-tui`
Baseline: `bce4152` (Phase 5.2 signed)
Plan: `docs/superpowers/plans/2026-06-25-phase5-3-pi-inspired-cli-ux.md`
Design: `docs/superpowers/specs/2026-06-25-phase5-3-pi-inspired-cli-ux-design.md`

## Start Order

1. Wait for Claude Code Task 0 commit (`test: capture Phase 5.3 CLI prompt regressions`).
2. Implement Tasks 1–6 in order, one commit per task.
3. After each commit, stop and give Gemini the hash for adversarial review.
4. After Claude Code Task 7 docs, support Task 8 integration smoke.

## Required Commits

1. `feat: add Pi-inspired CLI prompt flags`
2. `feat: route CLI prompts through Supervisor chat.send`
3. `feat: add CLI text and JSON output modes`
4. `feat: add CLI --continue goal binding`
5. `feat: add read-only coordinator config command`
6. `feat: open TUI after CLI prompt by default`

## Key Implementation Notes

- Reuse `tui_launcher.resolve_git_root`, `ensure_supervisor`, and project registry
  lookup — do not fork project resolution logic.
- `build_parser()` already uses subparsers; prompt flags must not shadow
  `supervisor`, `project`, `migrate`, etc. Consider `nargs=REMAINDER` or a
  dedicated pre-parse only when argv has no known subcommand.
- Legacy `coordinator chat` (`_cmd_chat`) stays untouched in 5.3; new path is
  headless Supervisor client in `cli_chat.py`.
- Print mode slash parity: leading `/status` should call `project.status` RPC,
  not Commander (match TUI `submitDecision` behavior).
- JSON envelope is a **public CLI schema**, not raw Supervisor protocol dumps.

## Evidence Required Per Commit

- focused failing test before implementation;
- focused passing test after implementation;
- `git diff --check`;
- changed-file list;
- known limitations;
- exact hash sent to Gemini.

## Verification Before Claiming Done

```bash
PYTHONPATH=src python3 -m unittest tests.test_cli_prompt -v
PYTHONPATH=src python3 -m unittest discover -s tests -q
```

Real smoke (polymarket):

```bash
coordinator --print -p "你好"
coordinator --mode json -p "现在有什么任务？"
coordinator --continue -p "生成一个只读验收任务"
coordinator config
```

Do not claim Phase 5.3 complete until Claude Code gate recording and Codex Gate E
both PASS.