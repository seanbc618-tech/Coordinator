CREATE TABLE IF NOT EXISTS project_backlog_items (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    goal_id INTEGER,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    rationale TEXT NOT NULL DEFAULT '',
    acceptance_criteria_json TEXT NOT NULL DEFAULT '[]',
    verification_commands_json TEXT NOT NULL DEFAULT '[]',
    execution_policy TEXT NOT NULL DEFAULT 'normal',
    priority INTEGER NOT NULL DEFAULT 50,
    status TEXT NOT NULL DEFAULT 'candidate',
    dedupe_key TEXT NOT NULL,
    linked_task_id TEXT,
    rejection_reason TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    admitted_at TEXT,
    completed_at TEXT,
    FOREIGN KEY(goal_id) REFERENCES goals(id),
    FOREIGN KEY(linked_task_id) REFERENCES tasks(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_project_backlog_dedupe_open
ON project_backlog_items(project_id, goal_id, dedupe_key)
WHERE status IN ('candidate', 'ready', 'admitted');

CREATE INDEX IF NOT EXISTS idx_project_backlog_project_status
ON project_backlog_items(project_id, status, priority, created_at);

CREATE TABLE IF NOT EXISTS task_evaluations (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    goal_id INTEGER,
    task_id TEXT NOT NULL,
    evaluator_id TEXT NOT NULL,
    verdict TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    evidence_json TEXT NOT NULL DEFAULT '{}',
    next_action TEXT NOT NULL DEFAULT 'none',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(task_id, evaluator_id)
);

CREATE INDEX IF NOT EXISTS idx_task_evaluations_project
ON task_evaluations(project_id, goal_id, created_at);

CREATE TABLE IF NOT EXISTS loop_iterations (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    goal_id INTEGER,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at TEXT,
    decision TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    evaluated_count INTEGER NOT NULL DEFAULT 0,
    admitted_count INTEGER NOT NULL DEFAULT 0,
    generated_count INTEGER NOT NULL DEFAULT 0,
    caps_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_loop_iterations_project
ON loop_iterations(project_id, goal_id, started_at);