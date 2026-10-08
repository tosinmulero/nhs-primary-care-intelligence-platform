-- SIMULATION ONLY: non-identifiable aggregated reporting, not clinical decision support.
select task_type, task_status, due_bucket, task_count
from {{ source('phase6', 'v_phase6_task_performance') }}
