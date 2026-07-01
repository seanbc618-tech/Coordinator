CREATE TABLE IF NOT EXISTS approval_requests (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    operator_item_id TEXT,
    action_method TEXT NOT NULL,
    action_params_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',
    token_hash TEXT NOT NULL,
    token_hint TEXT NOT NULL DEFAULT '',
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    decided_at TEXT,
    decided_by TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_approval_requests_project
ON approval_requests(project_id, status, created_at);

CREATE UNIQUE INDEX IF NOT EXISTS idx_approval_requests_token_hash
ON approval_requests(token_hash);

CREATE TABLE IF NOT EXISTS approval_channel_configs (
    id TEXT PRIMARY KEY,
    channel_type TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0,
    project_id TEXT,
    min_severity TEXT NOT NULL DEFAULT 'warning',
    config_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approval_deliveries (
    id TEXT PRIMARY KEY,
    approval_request_id TEXT NOT NULL,
    channel_config_id TEXT,
    project_id TEXT NOT NULL,
    channel_type TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_approval_deliveries_request
ON approval_deliveries(approval_request_id, created_at);

CREATE TABLE IF NOT EXISTS approval_audit_events (
    id TEXT PRIMARY KEY,
    approval_request_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    data_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);