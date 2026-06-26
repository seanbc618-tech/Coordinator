-- Scope Commander goals per project (at most one non-terminal goal per project_id).

drop index if exists idx_goals_nonterminal;

alter table goals add column project_id text not null default 'legacy-default';

create unique index if not exists idx_goals_nonterminal_per_project
    on goals(project_id)
    where status in ('draft', 'active', 'paused', 'blocked');