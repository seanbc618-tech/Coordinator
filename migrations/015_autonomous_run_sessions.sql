CREATE TABLE IF NOT EXISTS autonomous_run_sessions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    goal_id INTEGER,
    status TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'continuous',
    started_by TEXT NOT NULL DEFAULT 'operator',
    max_iterations INTEGER NOT NULL DEFAULT 100,
    max_runtime_seconds INTEGER NOT NULL DEFAULT 28800,
    idle_backoff_seconds INTEGER NOT NULL DEFAULT 30,
    max_idle_iterations INTEGER NOT NULL DEFAULT 12,
    iteration_count INTEGER NOT NULL DEFAULT 0,
    idle_iteration_count INTEGER NOT NULL DEFAULT 0,
    failure_count INTEGER NOT NULL DEFAULT 0,
    last_decision TEXT,
    last_reason TEXT,
    next_tick_after TEXT,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_heartbeat_at TEXT,
    ended_at TEXT,
    stop_reason TEXT,
    FOREIGN KEY(goal_id) REFERENCES goals(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_autonomous_run_one_active
ON autonomous_run_sessions(project_id)
WHERE status IN ('running', 'paused');

CREATE INDEX IF NOT EXISTS idx_autonomous_run_status_next_tick
ON autonomous_run_sessions(status, next_tick_after, project_id);

CREATE TABLE IF NOT EXISTS autonomous_run_steps (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    goal_id INTEGER,
    loop_iteration_id TEXT,
    decision TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    evaluated_count INTEGER NOT NULL DEFAULT 0,
    admitted_count INTEGER NOT NULL DEFAULT 0,
    generated_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(run_id) REFERENCES autonomous_run_sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_autonomous_run_steps_run
ON autonomous_run_steps(run_id, created_at);