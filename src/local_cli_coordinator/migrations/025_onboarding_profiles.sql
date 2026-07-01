CREATE TABLE IF NOT EXISTS project_profile_runs (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    repo_path TEXT NOT NULL,
    detected_profile TEXT NOT NULL,
    recommended_preset TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.0,
    findings_json TEXT NOT NULL DEFAULT '[]',
    verify_commands_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_project_profile_runs_repo
ON project_profile_runs(repo_path, created_at);

CREATE TABLE IF NOT EXISTS onboarding_runs (
    id TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    profile_name TEXT NOT NULL,
    preset_name TEXT NOT NULL,
    project_id TEXT,
    repo_path TEXT NOT NULL,
    plan_json TEXT NOT NULL DEFAULT '{}',
    applied_json TEXT NOT NULL DEFAULT '{}',
    snapshot_id TEXT,
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_onboarding_runs_project
ON onboarding_runs(project_id, created_at);

CREATE TABLE IF NOT EXISTS config_snapshots (
    id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    project_id TEXT,
    config_dir TEXT NOT NULL,
    files_json TEXT NOT NULL DEFAULT '{}',
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_config_snapshots_project
ON config_snapshots(project_id, created_at);

CREATE TABLE IF NOT EXISTS fleet_rollout_runs (
    id TEXT PRIMARY KEY,
    root_path TEXT NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    discovered_json TEXT NOT NULL DEFAULT '[]',
    applied_json TEXT NOT NULL DEFAULT '[]',
    skipped_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    finished_at TEXT
);