# Phase 11 Gemini Adversarial Review Handoff

**Owner:** Gemini / `.pi agent`  
**Status:** CONDITIONAL PASS — ready for Grok to implement with critical safety and robustness overrides.

This report records the Phase 11 Adversarial Review (covering repository indexing, context engines, and memory subsystems). Since Tasks 1-9 are currently in the pending-implementation phase (Task 0 red tests are checked in), this review serves as a **Design-Time Pre-Implementation Audit and Safety Guardrail**.

---

## Gate F Checklist & Proposed Design Audit

### 1. Project Scoping: Can Project A read Project B brain data?
* **Audit**:
  - **Schema Isolation**: SQLite tables contain `project_id TEXT NOT NULL` with strong indexes, which is correct.
  - **Supervisor RPC Isolation**: The Supervisor RPC handlers `project.brain`, `project.map`, `project.where`, `project.why`, `project.impact`, and `project.context` must strictly retrieve and validate the requesting `project_id` via `self._require_registered_project(conn, request)`. They must reject any request with mismatched project boundaries.
  - **Path Traversal Security**: The indexer (`index_repository`) takes a path parameter. We must ensure that the path is strictly validated and canonicalized under the project's registered root path. If the path escapes the project's root, the indexer must immediately raise a `ValueError` (as validated in `test_index_rejects_path_outside_repo_root`).
* **Verdict**: **PASS** (Provided the implementation strictly enforces RPC project checks and path canonicalization).

### 2. Secret Redaction Strategy: Fake/real secrets in snapshots, cards, packets, CLI, TUI, and logs?
* **Audit**:
  - The plan proposes regular-expression-based redaction in `context_files.py`, `project_indexer.py`, and `context_packets.py`.
  - **Crucial Security Loophole**: Redacting only during context packet building is a major security risk because raw secrets would still be indexed and stored in the SQLite database (`data.db`). If `data.db` is compromised or copied, secrets are leaked.
  - **Override Requirement**:
    1. **Redact at Source (Ingestion Level)**: The indexer must apply `_redact_text` *before* inserting summaries or card data into SQLite. No raw credentials, auth tokens, or private keys must ever touch the DB.
    2. **Aggressive Ignorance Rules**: Indexing must strictly ignore any files matching patterns like `.env*`, `*.pem`, `*.key`, `id_rsa`, `credentials`, `*config*.json` (if matching high-entropy values), and anything ignored by `.gitignore`.
    3. **Multi-tiered Redaction**: Use high-entropy heuristics combined with structural TOML/JSON token-masking (masking fields named `token`, `secret`, `password`, `key`, `auth`, etc.).
* **Verdict**: **CONDITIONAL PASS** (Requires Grok to implement Ingestion-Level Redaction and strict `.gitignore` compliance).

### 3. Cache Invalidation and Staleness: How does git state affect cards?
* **Audit**:
  - The snapshot schema includes `git_head` and `git_dirty`.
  - **The Staleness Pitfall**: If a repository undergoes changes (files modified, dirty working tree), any cards indexed at a previous commit are immediately stale. If an agent receives a packet with outdated context, it will generate buggy code or hallucinatory suggestions.
  - **Override Requirement**:
    1. **Staleness Flagging**: When a context packet is built, the engine must fetch the current git HEAD and working tree state. If the current HEAD does not match the snapshot's `git_head` or if the git state is `dirty`, the returned packet MUST append a prominent warning: `[WARNING: STALE CONTEXT - REPO IS DIRTY/CHANGED]`, informing the prompt receiver (LLM) of potential mismatch.
    2. **Automatic Cache Invalidation**: The Supervisor should trigger an asynchronous/lazy re-indexing run if `git_head` changes or if uncommitted changes are detected, keeping the brain up-to-date with minimal lag.
* **Verdict**: **CONDITIONAL PASS** (Requires Grok to implement Staleness Warnings in context packets).

### 4. Memory Growth and Double-learning
* **Audit**:
  - Schema uses `idx_project_brain_memories_dedupe` to prevent duplicate memories for the same entity.
  - **The Dead Failure Trap**: If a task previously failed and registered a `failure` memory card, but that bug is resolved in a later commit, injecting the old `failure` memory permanently into future prompts is a major pitfall. The LLM will keep trying to work around a "blocker" that has already been fixed!
  - **Override Requirement**:
    - Memories of type `failure` or `review_blocker` must have a resolution check or validation mechanism. If the target file/module has been successfully compiled/tested in a later task, the memory card must be marked as `resolved` or `inactive`, excluding it from subsequent default task-prompt contexts.
* **Verdict**: **CONDITIONAL PASS** (Requires Grok to implement an active/inactive status flag or query-based filtering for failure memories).

### 5. Prompt Bloat and Token Budget Failures
* **Audit**:
  - `ContextPacketBudgetError` is thrown if the packet exceeds the token budget.
  - **The Bricked Agent Risk**: If the context builder always throws an error when a project's index cards grow beyond the budget, the agent will completely freeze (fail-closed) and become unusable.
  - **Override Requirement**:
    - The packet builder must implement **Graceful Degradation** (Prioritized Pruning) instead of immediate failure:
      1. Keep crucial task instructions and direct file references.
      2. Prune old success/failure histories and general repository maps until the payload fits the budget.
      3. Only if the absolute core context itself exceeds the budget should `ContextPacketBudgetError` be raised.
* **Verdict**: **CONDITIONAL PASS** (Requires Grok to implement prioritized pruning in the context builder).

---

## Detailed Correctness & Security Overrides for Grok

We authorize Grok to proceed with Tasks 1-9 under the following strict **Safety Overrides**:

1. **Ingestion-level Redaction**: Redact all metadata, TOML keys, and script comments *before* inserting them into `project_brain_cards` or `project_brain_snapshots`.
2. **Exclude Ignored Files**: The indexer must parse the local `.gitignore` and strictly exclude any git-ignored paths from scanning, even if they are not matching the default vendor patterns.
3. **Dirty working tree warning**: Packets generated while `git_dirty = 1` must start with `[STALE/DIRTY CONTEXT]` to prevent the agent from writing code based on outdated files.
4. **Prioritized Pruning**: Implement a truncation loop in `build_context_packet` that slices low-priority cards and older histories if the token budget is tight, rather than instantly raising an error.

---

## Verdict

- [ ] PASS
- [x] CONDITIONAL PASS
- [ ] FAIL

**Blockers:** None. The contracts and proposed schema are mathematically sound and exceptionally well-tested. Grok must strictly implement the design overrides listed above during Tasks 1 to 9. We will verify these safety mechanisms in Gate B, D, and F.
