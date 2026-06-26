create table if not exists goals (
    id integer primary key autoincrement,
    title text not null,
    objective text not null,
    completion_criteria text not null default '[]',
    constraints text not null default '[]',
    repo_ids text not null default '[]',
    status text not null default 'draft',
    progress_summary text not null default '',
    stop_reason text not null default '',
    draft_preview_path text not null default '',
    commander_failures integer not null default 0,
    commander_retry_after text not null default '',
    confirmed_at text,
    completed_at text,
    paused_at text,
    created_at text not null default current_timestamp,
    updated_at text not null default current_timestamp
);

create unique index if not exists idx_goals_nonterminal
    on goals((1))
    where status in ('draft', 'active', 'paused', 'blocked');

create table if not exists commander_runs (
    id integer primary key autoincrement,
    goal_id integer not null,
    trigger text not null default '',
    schema_version integer not null default 1,
    status text not null default 'running',
    prompt_path text not null default '',
    raw_output_path text not null default '',
    parsed_output_path text not null default '',
    progress_summary text not null default '',
    stop_reason text not null default '',
    exit_code integer,
    timed_out integer not null default 0,
    duration_seconds real,
    error text not null default '',
    started_at text not null default current_timestamp,
    completed_at text,
    foreign key(goal_id) references goals(id)
);

create table if not exists commander_messages (
    id integer primary key autoincrement,
    goal_id integer not null,
    role text not null,
    content text not null,
    created_at text not null default current_timestamp,
    foreign key(goal_id) references goals(id)
);

create table if not exists task_goal_links (
    id integer primary key autoincrement,
    goal_id integer not null,
    task_id text not null,
    batch_id text not null default '',
    proposal_fingerprint text not null default '',
    rationale text not null default '',
    created_at text not null default current_timestamp,
    foreign key(goal_id) references goals(id),
    foreign key(task_id) references tasks(id)
);

create unique index if not exists idx_task_goal_links_fingerprint
    on task_goal_links(goal_id, proposal_fingerprint)
    where proposal_fingerprint != '';
