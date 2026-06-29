CREATE TABLE IF NOT EXISTS project_milestones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    goal_id INTEGER,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    priority INTEGER NOT NULL DEFAULT 0,
    success_criteria_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS task_recovery_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    attempt_id INTEGER,
    proposal_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    title TEXT NOT NULL,
    rationale TEXT NOT NULL,
    verification_commands_json TEXT NOT NULL DEFAULT '[]',
    dedupe_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    admitted_backlog_id INTEGER
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_recovery_open_dedupe
ON task_recovery_proposals(project_id, task_id, dedupe_key)
WHERE status IN ('pending', 'admitted');

CREATE TABLE IF NOT EXISTS agent_scorecards (
    agent_id TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    successes INTEGER NOT NULL DEFAULT 0,
    failures INTEGER NOT NULL DEFAULT 0,
    timeouts INTEGER NOT NULL DEFAULT 0,
    cancellations INTEGER NOT NULL DEFAULT 0,
    avg_runtime_seconds REAL,
    last_success_at TEXT,
    last_failure_at TEXT,
    cooldown_until TEXT
);

CREATE TABLE IF NOT EXISTS overnight_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    run_session_id INTEGER,
    window_started_at TEXT NOT NULL,
    window_ended_at TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

ALTER TABLE project_backlog_items ADD COLUMN milestone_id INTEGER;
ALTER TABLE loop_iterations ADD COLUMN milestone_id INTEGER;
ALTER TABLE tasks ADD COLUMN recovery_proposal_id INTEGER;