CREATE TABLE IF NOT EXISTS agent_capability_profiles (
    agent_id TEXT PRIMARY KEY,
    role TEXT NOT NULL DEFAULT '',
    skills_json TEXT NOT NULL DEFAULT '[]',
    risk_tier TEXT NOT NULL DEFAULT 'normal',
    review_strength TEXT NOT NULL DEFAULT 'unknown',
    max_task_minutes INTEGER NOT NULL DEFAULT 30,
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_benchmark_runs (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    benchmark_name TEXT NOT NULL,
    fixture_name TEXT NOT NULL,
    status TEXT NOT NULL,
    score REAL NOT NULL DEFAULT 0.0,
    duration_seconds REAL NOT NULL DEFAULT 0.0,
    result_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_route_decisions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    selected_agent_id TEXT NOT NULL,
    candidate_scores_json TEXT NOT NULL DEFAULT '[]',
    reason TEXT NOT NULL DEFAULT '',
    fallback_from_agent_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_fallback_edges (
    id TEXT PRIMARY KEY,
    from_agent_id TEXT NOT NULL,
    to_agent_id TEXT NOT NULL,
    capability_filter_json TEXT NOT NULL DEFAULT '[]',
    max_hops INTEGER NOT NULL DEFAULT 1,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);