# Commander Runtime Recovery Design

## Problem

The configured Codex command uses the removed `--ask-for-approval` option, so
every real Commander invocation exits with code 2. The runner hides stderr,
failed previews can still be confirmed, and chat text is not sent to Commander.

## Design

Use the installed Codex CLI's supported read-only invocation. Preserve stderr in
the run artifact and concise error so failures are actionable. Confirmation must
require the latest initial preview to have succeeded. Chat text is persisted as
a user message, invokes Commander with a `chat` trigger, and prints its progress
summary; proposed tasks continue through the existing admission policy.

## Acceptance

- Configured Codex planner and Commander commands contain only supported flags.
- A failing Commander reports stderr and cannot be confirmed.
- Chat text reaches Commander and receives a visible response.
- A real goal preview and daemon cycle produce admitted tasks on this machine.
- The complete unit test suite remains green.
