create table if not exists schema_migrations (
    version text primary key,
    applied_at text not null default current_timestamp
);

create table if not exists tasks (
    id text primary key,
    title text not null,
    repo text not null,
    state text not null,
    priority text not null,
    capabilities text not null,
    source_path text not null,
    goal text not null,
    acceptance_criteria text not null,
    verification_commands text not null,
    branch text not null default '',
    worktree_path text not null default '',
    created_at text not null default current_timestamp,
    updated_at text not null default current_timestamp
);

create table if not exists attempts (
    id integer primary key autoincrement,
    task_id text not null,
    agent_id text not null,
    command text not null,
    started_at text not null default current_timestamp,
    ended_at text,
    exit_code integer,
    log_path text not null default '',
    foreign key(task_id) references tasks(id)
);

create table if not exists events (
    id integer primary key autoincrement,
    task_id text not null,
    old_state text not null,
    new_state text not null,
    note text not null default '',
    created_at text not null default current_timestamp,
    foreign key(task_id) references tasks(id)
);

create table if not exists artifacts (
    id integer primary key autoincrement,
    task_id text not null,
    kind text not null,
    path text not null,
    created_at text not null default current_timestamp,
    foreign key(task_id) references tasks(id)
);

create table if not exists agents (
    id text primary key,
    capabilities text not null,
    max_concurrency integer not null,
    observed_successes integer not null default 0,
    observed_failures integer not null default 0
);

create table if not exists repos (
    id text primary key,
    path text not null,
    default_branch text not null,
    remote text not null,
    branch_prefix text not null,
    allow_push integer not null,
    merge_policy text not null
);
