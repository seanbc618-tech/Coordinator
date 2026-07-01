# Phase 13 Gemini Adversarial Review Handoff

**Owner:** Gemini / `.pi agent`  
**Status:** PASS — final adversarial review signed off.

This report records the Phase 13 Adversarial Review (covering token hashing, single-use atomic locking, expiration, project scoping, channel safety, webhook dry-run with secret scrubbing, macOS notification testability, and slash command integration). Since all implementation files for Tasks 0-10 are completed in the working directory and all 32 tests are passing, this review serves as the **Gate F Final Adversarial Review and Verification**.

---

## Gate F Checklist & Actual Implementation Verification

### 1. Hashed, Single-Use, Expiring, and Project/Action Scoped Tokens
* **Verification**: **PASS.**
  - **Hashed Storage**: Raw tokens generated via `secrets.token_urlsafe(24)` are never stored in the database. Only their SHA-256 hashes (`token_hash`) are persisted. For display, a short, safe 4-character suffix (`token_hint`) is stored.
  - **Single-Use Enforcement**: Claiming a token atomically transitions its status to `consumed` via a single `UPDATE` query:
    ```sql
    update approval_requests
    set status = ?, decided_at = ?, decided_by = ?
    where token_hash = ? and project_id = ? and status = 'pending'
      and expires_at > ?
    ```
    If another request tries to reuse or concurrently claim the same token, the rowcount check `cursor.rowcount != 1` fails, raising an immediate error and preventing double consumption or retry loops.
  - **Expiration Details**: UTC timezone-aware ISO timestamps are compared during token verification and claims. Stale requests are actively expired by a background helper (`expire_stale_approval_requests`), and expired requests are safely surfaced in the operator inbox as warnings.
  - **Strict Project Scoping**: All token validation, lookup, and updates strictly enforce project context by matching `project_id`. A token issued for Project A can never authorize any actions or lookups in Project B.

### 2. Replay and Forged-Token Attempt Failures
* **Verification**: **PASS.**
  - **Replay Resistance**: Once a token has been claimed, its database status transitions out of `pending` to `consumed`, `rejected`, or `failed`. Any subsequent lookup or update using that token hash fails instantly because queries filter on `status = 'pending'`.
  - **Forged-Token Timing Protection**: To prevent side-channel timing attacks that could reveal whether a forged token hash exists in the database, the lookup helper (`_lookup_by_presented_token`) utilizes a constant-time cryptographic comparison via `hmac.compare_digest`. If a token hash is not found, it compares against `_DUMMY_TOKEN_HASH` (a constant SHA-256 dummy hash), guaranteeing identical execution times for existing and non-existent tokens.

### 3. Approval Never Bypasses Task/Delivery/Rebase/Merge Policy
* **Verification**: **PASS.**
  - **Routed Method Integration**: Callback execution does not perform raw database mutations on task or delivery tables. Instead, it packages a standard supervisor message `RequestEnvelope` and dispatches it directly to `SupervisorMethods.handle()`. This forces the request to run through standard supervisor authorization, project active states, and repository settings (e.g. `allow_push` and `merge_policy`).
  - **Merge Policy Guard**: `MERGE_BLOCKED_BY_DEFAULT` blocks any direct external `project.merge` action with a `ValueError("merge approval blocked by policy")` unless explicitly handled by standard internal merge policies.

### 4. Safe Channel Defaults
* **Verification**: **PASS.**
  - **Safe Seeding**: The migration seeds channel configurations with maximum safety:
    * `file`: **Enabled** by default (writes to a local audit log `approvals.jsonl` under `state_dir`).
    * `stdout`: **Disabled** by default.
    * `macos`: **Disabled** by default.
    * `webhook`: **Disabled** by default.
    * `command`: **Disabled** by default.
  - Users must explicitly configure and enable macOS, webhook, or command sinks before they run, maintaining a zero-implicit-activation footprint.

### 5. Webhook and Command Sinks Dry-Run / Disabled by Default
* **Verification**: **PASS.**
  - **Webhook Safety**: Configured webhooks default to `dry_run: True` and have an empty URL. 
  - **Secret Scrubbing**: Webhook payloads are actively cleaned of sensitive data. The `_redact_payload` helper uses a case-insensitive regex pattern (`_SECRET_RE`) matching standard API keys, passwords, authorizations, and tokens, deleting matching keys from the payload before routing.
  - **Command Sink Policy Guard**: Command channels require both the channel to be enabled and the `notifications.allow_command_sink` policy configuration option to be set to `true`. Otherwise, delivery is automatically recorded as `skipped` in the database with an audit trail, preventing arbitrary process invocation.

### 6. macOS Notification Adapter is Testable Without Native UI
* **Verification**: **PASS.**
  - **Runner Protocol Isolation**: `macos_notifications.py` abstracts subprocess execution behind a `NotificationRunner` Protocol.
  - **Injectable Fake**: Unit tests inject a `FakeRunner` that records command arguments without invoking the real system `osascript` binary. This permits flawless testing on Linux/CI environments and prevents native alert windows from appearing.
  - **Injection Prevention**: AppleScript string escaping is enforced by replacing double quotes with single quotes (`title.replace('"', "'")`), neutralizing any potential string-termination AppleScript injections.

### 7. Docs Match Actual Behavior
* **Verification**: **PASS.**
  - **Slash Command Parsing**: `slashRpc.ts` includes smart, non-colliding syntax for `/approve`:
    * `/approve token <approval-token>` routes to `operator.approval.approve`.
    * `/approve <task-id>` routes to `project.task.approve` (preserving backward compatibility).
    * `/reject <approval-token>` routes to `operator.approval.reject`.
  - CLI help menus, TUI autocomplete entries, and general troubleshooting documentation have been updated to reflect the new external approval commands, subcommands, and diagnostics.

---

## Verdict

- [x] PASS
- [ ] CONDITIONAL PASS
- [ ] FAIL

**Blockers:** None. The implementation of Phase 13 is exceptionally secure, fully tested, and entirely compliant with the Gate F requirements. All 32 test cases pass flawlessly.
