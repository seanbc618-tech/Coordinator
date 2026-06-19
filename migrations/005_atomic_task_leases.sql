create unique index if not exists idx_task_leases_active_task
    on task_leases(task_id) where released_at is null;