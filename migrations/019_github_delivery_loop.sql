CREATE TABLE IF NOT EXISTS delivery_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    task_id TEXT,
    repo_id TEXT NOT NULL,
    branch_name TEXT NOT NULL,
    base_branch TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'github',
    status TEXT NOT NULL DEFAULT 'draft',
    pr_number INTEGER,
    pr_url TEXT,
    last_check_state TEXT,
    merge_ready INTEGER NOT NULL DEFAULT 0,
    requires_human_review INTEGER NOT NULL DEFAULT 1,
    evidence_packet_path TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_delivery_records_project
ON delivery_records(project_id, status, updated_at);

CREATE UNIQUE INDEX IF NOT EXISTS idx_delivery_records_open_branch
ON delivery_records(project_id, repo_id, branch_name)
WHERE status IN ('draft', 'pushed', 'pr_open', 'ci_pending', 'ci_failed', 'ready');

CREATE TABLE IF NOT EXISTS delivery_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    delivery_id INTEGER NOT NULL,
    project_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    status TEXT NOT NULL,
    data_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_delivery_events_delivery
ON delivery_events(delivery_id, created_at);