-- Add result classification and fallback lineage to attempts.

alter table attempts add column result_class text not null default '';
alter table attempts add column result_reason text not null default '';
alter table attempts add column fallback_from_attempt_id integer
    references attempts(id);

create index if not exists idx_attempts_task_id_id
    on attempts(task_id, id);
