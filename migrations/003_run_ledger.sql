create table if not exists daemon_runs (
    id integer primary key autoincrement,
    started_at text not null default current_timestamp,
    ended_at text,
    tasks_processed integer not null default 0,
    failures integer not null default 0,
    stop_reason text
);
