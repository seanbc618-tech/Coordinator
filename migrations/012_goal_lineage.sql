alter table goals add column parent_goal_id integer references goals(id);
create index if not exists idx_goals_parent_goal on goals(parent_goal_id);