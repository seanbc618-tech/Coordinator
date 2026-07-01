CREATE TABLE IF NOT EXISTS backup_records (
    id TEXT PRIMARY KEY,
    backup_path TEXT NOT NULL,
    status TEXT NOT NULL,
    manifest_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS upgrade_preflight_runs (
    id TEXT PRIMARY KEY,
    from_version TEXT NOT NULL DEFAULT '',
    to_version TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    findings_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS extension_manifests (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    manifest_path TEXT NOT NULL,
    status TEXT NOT NULL,
    capabilities_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);