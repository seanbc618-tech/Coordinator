CREATE TABLE IF NOT EXISTS preference_observations (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    observation_type TEXT NOT NULL,
    subject TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    redaction_status TEXT NOT NULL DEFAULT 'clean',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS preference_rules (
    id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    project_id TEXT,
    rule_type TEXT NOT NULL,
    status TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    rule_json TEXT NOT NULL DEFAULT '{}',
    evidence_ids_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_preference_rules_scope
ON preference_rules(scope, project_id, status, priority);