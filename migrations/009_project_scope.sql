-- Add project_id to execution tables for multi-project isolation.

-- Tasks
alter table tasks add column project_id text not null default 'legacy-default';

-- Events
alter table events add column project_id text not null default 'legacy-default';

-- Artifacts
alter table artifacts add column project_id text not null default 'legacy-default';

-- Daemon runs
alter table daemon_runs add column project_id text not null default 'legacy-default';

-- Task leases
alter table task_leases add column project_id text not null default 'legacy-default';

-- Backfill events/artifacts/leases from their parent task's project_id
update events set project_id = (
    select project_id from tasks where tasks.id = events.task_id
) where project_id = 'legacy-default';

update artifacts set project_id = (
    select project_id from tasks where tasks.id = artifacts.task_id
) where project_id = 'legacy-default';

update task_leases set project_id = (
    select project_id from tasks where tasks.id = task_leases.task_id
) where project_id = 'legacy-default';

-- Indexes for project-scoped queries
create index if not exists idx_tasks_project_state on tasks(project_id, state);
create index if not exists idx_events_project on events(project_id, id);
create index if not exists idx_artifacts_project on artifacts(project_id);
create index if not exists idx_daemon_runs_project on daemon_runs(project_id);
create index if not exists idx_task_leases_project on task_leases(project_id);
