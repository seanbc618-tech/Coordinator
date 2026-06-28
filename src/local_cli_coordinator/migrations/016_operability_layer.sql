CREATE TABLE IF NOT EXISTS worker_state_snapshots (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    task_id TEXT,
    attempt_id INTEGER,
    agent_id TEXT,
    run_id TEXT,
    state_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    redaction_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_worker_state_project_task
ON worker_state_snapshots(project_id, task_id, created_at);

CREATE TABLE IF NOT EXISTS supervisor_events_v2 (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    name TEXT NOT NULL,
    source TEXT NOT NULL,
    actor TEXT,
    severity TEXT NOT NULL DEFAULT 'info',
    provenance TEXT NOT NULL DEFAULT 'supervisor',
    terminal_fingerprint TEXT,
    payload TEXT NOT NULL,
    legacy_cursor INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_supervisor_events_v2_project_seq
ON supervisor_events_v2(project_id, seq);

CREATE INDEX IF NOT EXISTS idx_supervisor_events_v2_name
ON supervisor_events_v2(name, created_at);