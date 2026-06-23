create table if not exists task_leases (
    id integer primary key autoincrement,
    task_id text not null references tasks(id),
    agent_id text not null,
    acquired_at text not null default current_timestamp,
    expires_at text not null,
    released_at text
);

create index if not exists idx_task_leases_task on task_leases(task_id);
create index if not exists idx_task_leases_agent on task_leases(agent_id);
create index if not exists idx_task_leases_active on task_leases(released_at, expires_at);
