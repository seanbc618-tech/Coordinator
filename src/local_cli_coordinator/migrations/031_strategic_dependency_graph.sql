CREATE TABLE IF NOT EXISTS roadmap_nodes (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    node_type TEXT NOT NULL,
    ref_table TEXT,
    ref_id TEXT,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    priority INTEGER NOT NULL DEFAULT 50,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, node_type, ref_table, ref_id)
);

CREATE TABLE IF NOT EXISTS roadmap_edges (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    from_node_id TEXT NOT NULL,
    to_node_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    rationale TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, from_node_id, to_node_id, relation)
);

CREATE TABLE IF NOT EXISTS roadmap_snapshots (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    source TEXT NOT NULL,
    summary_json TEXT NOT NULL DEFAULT '{}',
    graph_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_roadmap_nodes_project
    ON roadmap_nodes(project_id, status, priority);

CREATE INDEX IF NOT EXISTS idx_roadmap_edges_project
    ON roadmap_edges(project_id, from_node_id, to_node_id);

ALTER TABLE projects ADD COLUMN roadmap_graph_enabled INTEGER NOT NULL DEFAULT 0;