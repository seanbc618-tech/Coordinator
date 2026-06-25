alter table commander_runs add column context_manifest text not null default '[]';
alter table commander_runs add column execution_policy text not null default '{}';
alter table tasks add column execution_policy text not null default '{}';