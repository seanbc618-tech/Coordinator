-- Project registry for multi-project Supervisor.

create table if not exists projects (
    id text primary key,
    canonical_path text not null unique,
    repo_id text not null default '',
    default_branch text not null default 'main',
    branch_prefix text not null default 'coord/',
    verify_commands text not null default '',
    active integer not null default 1,
    created_at text not null default current_timestamp,
    updated_at text not null default current_timestamp
);

create index if not exists idx_projects_canonical_path
    on projects(canonical_path);
