# Phase 5.5 Planning Kickoff

Date: 2026-06-26
Branch: `external/coordinator-global-tui` (merge to `main` first, then branch)
Plan: `docs/superpowers/plans/2026-06-26-phase5-5-operational-ux.md`
Design spec: *(to create after Gemini review)* `docs/superpowers/specs/2026-06-26-phase5-5-operational-ux-design.md`

## Prerequisites

- [x] Phase 5.4 Gate C PASS (949/949, ResourceWarning=error)
- [x] Phase 5.4 acceptance handoff (`2026-06-26-phase5-4-acceptance.md`)
- [ ] Phase 5.4 merged to `main`
- [ ] Gemini adversarial review PASS or CONDITIONAL

## Red Test Suites (Claude)

| File | Wave | Tests | Status |
|------|------|-------|--------|
| `test_phase5_5_chat_persona.py` | A | 6 | 🔴 6 red |
| `test_phase5_5_task_detail.py` | A | 4 | 🔴 4 red |
| `test_phase5_5_log_tail.py` | B | 5 | 🔴 1 red, 4 guard |
| `test_phase5_5_task_control.py` | C | 10 | 🔴 5 red, 5 guard |
| `test_phase5_5_dashboard.py` | D | 5 | 🔴 4 red, 1 guard |
| `test_phase5_5_cleanup.py` | E | 8 | 🔴 0 red, 8 guard |
| **Total** | | **38** | **20 red, 18 guard** |

Guard tests assert error-path contracts (nonexistent task, unknown subcommand)
and will remain green. Red tests assert new features and will turn green as
Grok implements each wave.

## Wave Ownership

| Wave | Tasks | Owner | Gate |
|------|-------|-------|------|
| A: 总管对话 + 任务详情 | 0-3 | Claude (0,3), Grok (1,2) | Gate A: Codex |
| B: 实时日志 | 4-6 | Claude (6), Grok (4,5) | Gate B: Codex |
| C: 任务控制 | 7-9 | Claude (9), Grok (7,8) | Gate C: Codex |
| D: 多项目总览 | 10-12 | Claude (12), Grok (10,11) | Gate D: Codex |
| E: 安全清理 | 13-15 | Claude (14), Grok (13,15) | Gate E: Codex |

## Suggested Execution Order

```
Gemini review PASS
  → Claude 0: red tests committed (this kickoff)
  → Grok 1-3: chat persona + task detail → Codex Gate A
  → Grok 4-6: log tail RPC + TUI panel → Codex Gate B
  → Claude 7: docs update (cli.md, troubleshooting.md)
  → Grok 8-9: task approve/cancel/retry → Codex Gate C
  → Grok 10-12: dashboard RPC + TUI → Codex Gate D
  → Grok 13-15: safe cleanup/rollback → Codex Gate E (final)
```

## Open Questions (resolve in design spec)

1. Approve semantics: trigger merge/push or only unblock daemon?
2. Log tail: poll vs push events (`task.log.append`)?
3. Dashboard: TUI only, or also `coordinator supervisor dashboard` CLI?
4. Chinese copy for 总管 persona — fixed strings vs Commander-generated?
5. Cancel: default preserve worktree vs auto-cleanup after 24h?

## Dependencies on Phase 5.4

| 5.4 feature | 5.5 use |
|-------------|---------|
| `execution_policy` on tasks | Show in task detail; cancel respects stage |
| `context_manifest` | Show hashed file list in task detail (no content) |
| `--mode rpc` | Dashboard/log tail consumable by future automation |
| Goal lineage | Dashboard shows `parent_goal_id` on forked goals |

## Safety Matrix (Gemini must challenge)

| Action | Risk | Mitigation |
|--------|------|------------|
| Cancel running task | orphan worktree | lease release + explicit note; `--purge` later |
| Rollback task | lose uncommitted work | dry-run diff summary; require confirm token |
| Log tail | path traversal | artifact registry only; no arbitrary paths |
| Dashboard | cross-project leak | aggregate counts only; detail needs `project_id` |
| Approve | skip human review policy | only from `awaiting_human`; audit event |
