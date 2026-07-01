CREATE TABLE IF NOT EXISTS simulation_runs (
    id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    project_id TEXT,
    horizon_hours REAL NOT NULL DEFAULT 8.0,
    status TEXT NOT NULL,
    inputs_json TEXT NOT NULL DEFAULT '{}',
    report_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_simulation_runs_project
ON simulation_runs(project_id, created_at);

CREATE TABLE IF NOT EXISTS simulation_events (
    id TEXT PRIMARY KEY,
    simulation_run_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    project_id TEXT,
    task_id TEXT,
    data_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);