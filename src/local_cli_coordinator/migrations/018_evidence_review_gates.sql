CREATE TABLE IF NOT EXISTS task_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    attempt_id INTEGER,
    evidence_type TEXT NOT NULL,
    status TEXT NOT NULL,
    summary TEXT NOT NULL,
    data_json TEXT NOT NULL DEFAULT '{}',
    artifact_path TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_task_evidence_task
ON task_evidence(project_id, task_id, created_at);

CREATE TABLE IF NOT EXISTS task_review_verdicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    attempt_id INTEGER,
    reviewer_id TEXT NOT NULL,
    verdict TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.0,
    rationale TEXT NOT NULL,
    evidence_ids_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_task_review_verdicts_task
ON task_review_verdicts(project_id, task_id, created_at);

CREATE TABLE IF NOT EXISTS task_risk_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    reasons_json TEXT NOT NULL DEFAULT '[]',
    requires_human_review INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_task_risk_latest_unique
ON task_risk_assessments(project_id, task_id, created_at);

CREATE TABLE IF NOT EXISTS review_packets_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    packet_json_path TEXT NOT NULL,
    packet_markdown_path TEXT NOT NULL,
    verdict TEXT NOT NULL,
    created_at TEXT NOT NULL
);