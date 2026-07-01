CREATE TABLE IF NOT EXISTS diagnostic_runs (
    id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    project_id TEXT,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    findings_json TEXT NOT NULL DEFAULT '[]',
    repairs_json TEXT NOT NULL DEFAULT '[]',
    started_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_diagnostic_runs_project
ON diagnostic_runs(project_id, started_at);

CREATE TABLE IF NOT EXISTS repair_audit_events (
    id TEXT PRIMARY KEY,
    diagnostic_run_id TEXT NOT NULL,
    project_id TEXT,
    repair_key TEXT NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    before_json TEXT NOT NULL DEFAULT '{}',
    after_json TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS global_control_events (
    id TEXT PRIMARY KEY,
    action TEXT NOT NULL,
    scope TEXT NOT NULL,
    project_id TEXT,
    reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    affected_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_global_control_events_created
ON global_control_events(created_at);

CREATE TABLE IF NOT EXISTS agent_health_snapshots (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT '',
    project_id TEXT,
    status TEXT NOT NULL,
    window_started_at TEXT NOT NULL,
    window_finished_at TEXT NOT NULL,
    metrics_json TEXT NOT NULL DEFAULT '{}',
    recommendation TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_health_snapshots_agent
ON agent_health_snapshots(agent_id, created_at);

CREATE TABLE IF NOT EXISTS morning_handoffs (
    id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    project_id TEXT,
    from_time TEXT NOT NULL,
    to_time TEXT NOT NULL,
    summary_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_morning_handoffs_created
ON morning_handoffs(scope, project_id, created_at);

ALTER TABLE projects ADD COLUMN status TEXT NOT NULL DEFAULT 'active';
ALTER TABLE projects ADD COLUMN pause_reason TEXT NOT NULL DEFAULT '';