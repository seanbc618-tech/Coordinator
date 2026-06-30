# Phase 9 Gemini Adversarial Review Handoff

**Owner:** Gemini / `.pi agent`  
**Status:** PENDING — awaiting review after Grok Task 10 docs land.

## Checklist (Gate F)

1. Can `/deliver` push or create PR when `allow_push=false`?
2. Can human-review-required tasks report delivery-ready?
3. Can worker-controlled text forge PR body evidence or reviewer verdicts?
4. Can `gh` command args be shell-injected?
5. Can tests accidentally call live GitHub or require auth?
6. Can CI failure be hidden or classified as pass?
7. Can failed CI generate infinite recovery tasks?
8. Can PR records leak cross-project task titles or evidence?
9. Can #10-style stacked PRs be misread as main-ready without base validation?
10. Are docs consistent with actual CLI/TUI behavior?

## Verification commands

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_github_cli \
  tests.test_github_delivery \
  tests.test_delivery_policy \
  tests.test_delivery_recovery \
  tests.test_phase9_github_delivery_e2e -v
```

## Verdict

- [ ] PASS
- [ ] CONDITIONAL PASS
- [ ] FAIL

**Blockers:**

_(Gemini fills this section.)_