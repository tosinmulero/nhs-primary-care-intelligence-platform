-- Failing rows should be zero; synthetic data only.
with t as (select coalesce(sum(task_count),0) as n from {{ ref('mart_p7_admin_tasks') }}),
w as (select coalesce(sum(case_count),0) as n from {{ ref('mart_p7_workflow_queues') }})
select t.n as tasks, w.n as workflow_cases from t cross join w
where t.n <> 6600 or w.n <> 4749
