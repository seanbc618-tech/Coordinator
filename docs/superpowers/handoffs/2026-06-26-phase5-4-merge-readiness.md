# Phase 5.4 Merge Readiness Checklist

Date: 2026-06-26  
Branch: `external/coordinator-global-tui`  
Gate C: **PASS** (`4d9a8b6` tip)  
Review routing: **Gemini** → `docs/superpowers/handoffs/2026-06-26-phase5-4-gemini-review.md` · **Claude** doc nits + merge smoke notes

---

## 1. Scope — what merges

Phase 5.4 delivers three waves on the Phase 5.3 headless CLI + global Supervisor stack:

| Wave | Capability | Key modules |
|------|------------|-------------|
| A | `@file` context, manifest, redaction | `context_files.py`, migration 013 `context_manifest` |
| B | `--resume` / `--fork`, goal lineage | `goal_sessions.py`, migration 012 `parent_goal_id` |
| C | `--tools` / `--no-tools` / `--exclude-tools`, engine gates, `--mode rpc` | `execution_policy.py`, migration 013 `execution_policy` |

**Baseline:** Phase 5.3 at `bce4152` lineage on `external/coordinator-global-tui`.  
**Tip:** tracks `external/coordinator-global-tui` (Gemini review + P1/P2 fixes applied).

### Phase 5.4 wave commits (072ca26 … Gate C)

| Hash | Summary |
|------|---------|
| `072ca26` | goal session red tests |
| `8a59709` | goal resume/fork lineage |
| `7ff494f` | CLI resume/fork |
| `17eef6f` | Gate B P1 repair |
| `b5f43bf` | PTY/fork boundary tests |
| `5718b75` | Gate B re-sign handoff |
| `d3e2c33` | execution policy red tests |
| `a5ea7c6` | execution policy persistence |
| `ff4de36` | engine stages + RPC mode |
| `e621fbe` | Phase 5.4 E2E tests |
| `feed21c` | cli + troubleshooting docs |
| `687949d` | acceptance handoff (initial) |
| `9dc5f3e` | Task 11 integration gates |
| `3f6e1ca` | gate live-event thread join fix |
| `8307333` | leak regression tests |
| `4d9a8b6` | Gate C PASS handoff |
| `af7a082` | merge readiness + 5.5 plan draft |
| `51cd8dc` | Gemini review handoff |

### Post–Gate C / review commits (also on PR branch)

| Hash | Summary |
|------|---------|
| `0d3bc1a` | merge readiness commit count update |
| `9080e80` | 5.5 planning kickoff + cli.md merge banner |
| `3a87ede` | **P1** interactive resume prompt + RPC tool envelope |
| `b538dea` | revert 5.5 red tests (keep off merge PR) |
| `1e6a3ee` | Gemini adversarial review output |
| *(tip)* | RPC argparse envelope + doc reconciliation |

Earlier Phase 5.3/5.2 commits on this branch are already on `origin/main` lineage via the integration branch.

---

## 2. Branches / PRs — what to update

### Primary PR

| Item | Action |
|------|--------|
| **Source** | `external/coordinator-global-tui` |
| **Target** | `main` (or team default integration branch) |
| **Title** | `feat: Phase 5.4 context, sessions, tool controls, and RPC mode` |
| **Push** | `git push origin external/coordinator-global-tui` after each review-fix commit |
| **PR body** | Link `docs/superpowers/handoffs/2026-06-26-phase5-4-acceptance.md` + Gate C PASS block |

### Do **not** merge or push from these (local only / stale worktrees)

| Branch / path | Note |
|---------------|------|
| `run_attack_*.py`, `debug_*.py`, `child*.py`, `test_*.py` (repo root) | Untracked debug — **never stage** |
| `agent/grok/*`, `codex/*`, `claude/*` worktrees | Independent experiments; no Phase 5.4 dependency |
| `fix/tui-build-stable-manifest` | Separate line; rebase only if manifest conflict appears |

### Downstream after merge

| Consumer | Action |
|----------|--------|
| Operator machines | `pip install --force-reinstall` wheel or `PYTHONPATH=src` dev install |
| `polymarket-crypto-threshold` smoke | Re-run Phase 5.4 CLI examples from `docs/cli.md` |
| Phase 5.5 planning | Branch from updated `main` after merge |

---

## 3. Working tree hygiene (blocking for PR)

### Untracked files — delete or ignore locally; **do not commit**

```text
child.py, child2.py, child3.py
debug_paths.py, debug_test.py
run_attack_a.py, run_attack_a3.py, run_attack_a4.py
run_attack_c.py, run_attack_c2.py, run_attack_c_cli.py
run_attack_conflict.py, run_attack_json.py, run_attack_json2.py
run_attack_persist.py, run_attack_policy_json.py
run_attack_prompt_drop.py, run_attack_rpc.py, run_attack_rpc_root.py
run_attack_tty.py
run_failed_test.py, run_test_stderr.py
test_empty_resume.py, test_parse_error.py, test_script.py
```

**Recommended before push:**

```bash
# Option A: delete local scratch (preferred)
rm -f child*.py debug_*.py run_attack_*.py run_failed_test.py run_test_stderr.py \
      test_empty_resume.py test_parse_error.py test_script.py

# Option B: keep locally but ensure never staged
git status --short | rg '^\?\?'   # should be empty before PR open
```

### Staged-content guard

```bash
git diff --cached --name-only | rg 'run_attack|debug_|child[0-9]*\.py'
# must print nothing

git diff --check
# clean
```

---

## 4. Schema / migration checklist

Byte-identical pairs (already in branch):

| Migration | Adds |
|-----------|------|
| `012_goal_lineage.sql` | `goals.parent_goal_id` |
| `013_execution_context.sql` | `commander_runs.context_manifest`, `execution_policy`; `tasks.execution_policy` |

**Post-merge verify on fresh DB:**

```bash
PYTHONPATH=src python3 -m unittest tests.test_wheel_migrations -v
```

---

## 5. Pre-merge verification (operator rerun)

### Required (Gate C baseline)

```bash
npm run typecheck --prefix ui-tui
npm run lint --prefix ui-tui
npm test --prefix ui-tui -- --run

PYTHONWARNINGS=error::ResourceWarning PYTHONPATH=src python3 -m unittest \
  tests.test_cli_file_context tests.test_goal_sessions \
  tests.test_execution_policy tests.test_phase5_4_e2e tests.test_cli_prompt -v
# 156/156 OK

PYTHONWARNINGS=error::ResourceWarning PYTHONPATH=src python3 -m unittest discover -s tests -q
# 949/949 OK

git diff --check
```

### Recommended (merge confidence)

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_tui_bundle.WheelPackagingTest tests.test_wheel_migrations -v
# 3/3 OK

PYTHONWARNINGS=error::ResourceWarning PYTHONPATH=src python3 -m unittest \
  tests.test_phase5_4_e2e.LeakRegressionTests -v
# 4/4 OK
```

### Optional real-project smoke

```bash
cd /Users/xiafan/polymarket-crypto-threshold
coordinator @README.md --mode json -p "summarize"
coordinator --resume --mode json
coordinator --tools read,grep --mode rpc -p "/status"
```

---

## 6. Push decision matrix

| Question | Answer |
|----------|--------|
| Gate C PASS? | **Yes** — Codex 2026-06-26 |
| Unpushed commits? | **18** — push before PR |
| Dirty tracked files? | **No** |
| Untracked debug scripts? | **Yes** — clean locally, never push |
| ui-tui bundle rebuild needed? | Only if `ui-tui/` changed (not in Phase 5.4 Python delta) |
| Breaking CLI changes? | Additive flags only; default behavior unchanged |

**Push command:**

```bash
git push origin external/coordinator-global-tui
```

---

## 7. PR description skeleton

```markdown
## Phase 5.4 — Context, Sessions, Tool Controls, RPC

### Summary
- @file bounded context with double validation and manifest redaction
- --resume / --fork with project-scoped goal lineage
- --tools / --no-tools / --exclude-tools with engine stage enforcement
- --mode rpc for protocol-level ResponseEnvelope output

### Gates
- Gate A/B/C PASS (see handoff)
- 949 Python tests, 138 ui-tui tests
- Migrations 012 + 013

### Docs
- docs/cli.md, docs/troubleshooting.md
- docs/superpowers/handoffs/2026-06-26-phase5-4-acceptance.md

### Reviewers
- Gemini: adversarial checklist (this doc + acceptance handoff)
- Codex: already signed Gate C
```

---

## 8. Merge blockers (none open)

| Severity | Item | Status |
|----------|------|--------|
| P0 | — | none |
| P1 | Full-suite ResourceWarning leak | **Fixed** (`3f6e1ca`, `8307333`) |
| P2 | Untracked debug scripts | Operator hygiene — not a code defect |
| P2 | Handoff hash drift | **Fixed** (`9dc5f3e` in acceptance doc) |

---

## 9. Post-merge checklist

- [ ] Tag or release note referencing migrations 012/013
- [ ] Notify operators: new CLI flags documented in `docs/cli.md`
- [ ] Open Phase 5.5 plan (`docs/superpowers/plans/2026-06-26-phase5-5-operational-ux.md`)
- [ ] Gemini review → `docs/superpowers/handoffs/2026-06-26-phase5-4-gemini-review.md`
- [ ] Claude: changelog nits, install.md cross-links, merge smoke log