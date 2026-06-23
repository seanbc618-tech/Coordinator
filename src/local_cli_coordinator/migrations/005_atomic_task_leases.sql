update task_leases
set released_at = current_timestamp
where released_at is null
  and id not in (
    select max(id)
    from task_leases
    where released_at is null
    group by task_id
  );

create unique index if not exists idx_task_leases_active_task
    on task_leases(task_id) where released_at is null;