-- Phase 18: evidence artifact warehouse (rename legacy task artifact refs first).

ALTER TABLE artifacts RENAME TO task_artifacts;

DROP INDEX IF EXISTS idx_artifacts_project;
CREATE INDEX IF NOT EXISTS idx_task_artifacts_project ON task_artifacts(project_id);

CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    task_id TEXT,
    run_id TEXT,
    artifact_type TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    redaction_status TEXT NOT NULL DEFAULT 'unknown',
    provenance_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_artifacts_project_type
ON artifacts(project_id, artifact_type, created_at);

CREATE TABLE IF NOT EXISTS evidence_exports (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    scope TEXT NOT NULL,
    status TEXT NOT NULL,
    bundle_path TEXT NOT NULL,
    manifest_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS retention_runs (
    id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    project_id TEXT,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    plan_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    finished_at TEXT
);