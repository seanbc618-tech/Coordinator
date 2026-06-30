# Phase 10 Gemini Adversarial Review Handoff

**Owner:** Gemini / `.pi agent`  
**Status:** PENDING — awaiting review after Grok Task 10 docs land.

## Gate F Checklist

1. Can `/inbox` leak task titles or evidence from another project?
2. Can a stale operator item remain open after the source is resolved?
3. Can two different failures collapse into one dedupe key?
4. Can notification command sink run without explicit enablement?
5. Can command sink be shell-injected?
6. Can summaries leak prompts, tokens, env vars, or log bodies?
7. Can quiet hours suppress critical items?
8. Can `operator.decision` approve, retry, cancel, or deliver without using existing policy-gated RPCs?
9. Can a destructive decision execute without confirmation?
10. Are README, CLI docs, TUI docs, and actual slash behavior consistent?

## Verdict

- [ ] PASS
- [ ] CONDITIONAL PASS
- [ ] FAIL

**Blockers:** _(Gemini fills this section.)_