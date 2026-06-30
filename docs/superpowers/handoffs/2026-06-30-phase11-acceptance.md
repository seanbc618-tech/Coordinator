# Phase 11 Project Brain — Acceptance Handoff

**Branch:** `phase11-project-brain-context-engine`  
**Implementer:** Grok  
**Status:** Task 0 red tests landed — Gemini **CONDITIONAL PASS** (design-time audit)

## Gemini mandates (binding for Tasks 1–9)

See `docs/superpowers/handoffs/2026-06-30-phase11-gemini-review.md`:

1. Ingestion-level redaction before SQLite write
2. `.gitignore` + secret filename patterns excluded at index time
3. Stale/dirty git warnings in context packets
4. Prioritized pruning before `ContextPacketBudgetError`
5. Inactive failure memories excluded from default task prompts

Red tests amended in commit after Gemini review to encode these contracts.