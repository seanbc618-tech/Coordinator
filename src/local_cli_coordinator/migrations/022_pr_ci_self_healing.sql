CREATE TABLE IF NOT EXISTS pr_health_records (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    delivery_id INTEGER NOT NULL,
    pr_number INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'observed',
    head_branch TEXT NOT NULL,
    base_branch TEXT NOT NULL,
    head_sha TEXT NOT NULL DEFAULT '',
    base_sha TEXT NOT NULL DEFAULT '',
    merge_state TEXT NOT NULL DEFAULT 'unknown',
    ci_state TEXT NOT NULL DEFAULT 'unknown',
    review_state TEXT NOT NULL DEFAULT 'unknown',
    stale INTEGER NOT NULL DEFAULT 0,
    last_checked_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pr_health_project
ON pr_health_records(project_id, status, updated_at);

CREATE TABLE IF NOT EXISTS pr_healing_attempts (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    delivery_id INTEGER NOT NULL,
    pr_health_id TEXT NOT NULL,
    action TEXT NOT NULL,
    status TEXT NOT NULL,
    worktree_path TEXT NOT NULL DEFAULT '',
    evidence_path TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_pr_healing_attempts_health
ON pr_healing_attempts(pr_health_id, created_at);

CREATE TABLE IF NOT EXISTS ci_failure_records (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    delivery_id INTEGER NOT NULL,
    check_name TEXT NOT NULL,
    status TEXT NOT NULL,
    conclusion TEXT NOT NULL,
    failure_class TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    evidence_json TEXT NOT NULL DEFAULT '{}',
    recovery_task_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_ci_failure_dedupe
ON ci_failure_records(project_id, delivery_id, check_name, conclusion, failure_class);