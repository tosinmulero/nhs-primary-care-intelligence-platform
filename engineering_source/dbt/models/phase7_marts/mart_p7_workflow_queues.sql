-- SIMULATION ONLY: non-identifiable aggregated reporting, not clinical decision support.
select source_kind, case_status, case_count
from {{ source('phase6', 'v_phase6_workflow_queue') }}
