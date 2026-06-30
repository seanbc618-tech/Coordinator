CREATE TABLE IF NOT EXISTS project_brain_snapshots (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    repo_path TEXT NOT NULL,
    git_head TEXT NOT NULL DEFAULT '',
    git_dirty INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ready',
    summary TEXT NOT NULL DEFAULT '',
    file_count INTEGER NOT NULL DEFAULT 0,
    indexed_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_project_brain_snapshots_project
ON project_brain_snapshots(project_id, updated_at);

CREATE TABLE IF NOT EXISTS project_brain_cards (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    card_type TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    data_json TEXT NOT NULL DEFAULT '{}',
    citations_json TEXT NOT NULL DEFAULT '[]',
    confidence REAL NOT NULL DEFAULT 0.5,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_project_brain_cards_lookup
ON project_brain_cards(project_id, card_type, title);

CREATE TABLE IF NOT EXISTS project_context_packets (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    task_id TEXT,
    goal_id INTEGER,
    purpose TEXT NOT NULL,
    token_budget INTEGER NOT NULL,
    packet_json TEXT NOT NULL,
    redaction_report_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_project_context_packets_project
ON project_context_packets(project_id, created_at);

CREATE TABLE IF NOT EXISTS project_brain_memories (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    data_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_project_brain_memories_dedupe
ON project_brain_memories(project_id, source_type, source_id, memory_type, title);