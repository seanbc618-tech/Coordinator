-- Supervisor event stream for multi-project replay.

create table if not exists supervisor_events (
    id integer primary key autoincrement,
    project_id text not null,
    cursor integer not null,
    event_type text not null,
    payload text not null default '{}',
    created_at text not null default current_timestamp,
    unique(project_id, cursor)
);

create index if not exists idx_supervisor_events_project_cursor
    on supervisor_events(project_id, cursor);
