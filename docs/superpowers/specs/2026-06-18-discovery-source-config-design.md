# Discovery Source Configuration Design

## Scope

LE-12 adds configuration only. Discovery execution, finding persistence, and
planner integration remain in later tasks.

## Configuration Shape

Discovery sources live in optional `config/discovery.toml`. Each named source
owns its source type, an optional command for command-backed sources, and a
per-repository enabled flag.

```toml
[sources.inbox]
type = "inbox"

[sources.inbox.repos]
example = true
legacy = false
```

Supported source types are `inbox`, `git_recent_commits`, `command`,
`ci_command`, and `issue_command`.

## Application Model

`DiscoverySourceConfig` contains `id`, `type`, `repos`, and optional
`command`. `CoordinatorConfig.discovery_sources` is a dictionary keyed by
source id and defaults to an empty dictionary so existing direct constructors
remain compatible.

If `config/discovery.toml` is absent, configuration loading succeeds with no
discovery sources. Invalid source types, malformed repository maps, and
non-boolean repository flags raise `ValueError` with the source and offending
value in the message.

## Testing

Tests cover all supported types, per-repository true/false flags, optional-file
compatibility, command preservation, and clear rejection of invalid types and
repository flags.
