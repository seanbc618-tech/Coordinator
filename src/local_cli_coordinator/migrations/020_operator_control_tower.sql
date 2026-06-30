CREATE TABLE IF NOT EXISTS operator_items (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    action_label TEXT NOT NULL DEFAULT '',
    action_method TEXT,
    action_params_json TEXT NOT NULL DEFAULT '{}',
    dedupe_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_operator_items_dedupe
ON operator_items(project_id, dedupe_key)
WHERE status IN ('open', 'acknowledged');

CREATE INDEX IF NOT EXISTS idx_operator_items_project_status
ON operator_items(project_id, status, severity, updated_at);

CREATE TABLE IF NOT EXISTS notification_rules (
    id TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1,
    sink TEXT NOT NULL,
    min_severity TEXT NOT NULL DEFAULT 'warning',
    project_id TEXT,
    event_filter TEXT NOT NULL DEFAULT '*',
    quiet_start TEXT,
    quiet_end TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notification_deliveries (
    id TEXT PRIMARY KEY,
    rule_id TEXT NOT NULL,
    operator_item_id TEXT,
    project_id TEXT NOT NULL,
    sink TEXT NOT NULL,
    status TEXT NOT NULL,
    dedupe_key TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_notification_delivery_dedupe
ON notification_deliveries(rule_id, dedupe_key);

CREATE TABLE IF NOT EXISTS operator_summaries (
    id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    project_id TEXT,
    summary_kind TEXT NOT NULL,
    from_cursor INTEGER,
    to_cursor INTEGER,
    counts_json TEXT NOT NULL DEFAULT '{}',
    highlights_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);