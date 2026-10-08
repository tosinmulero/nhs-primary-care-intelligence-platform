-- SIMULATION ONLY: non-identifiable aggregated reporting, not clinical decision support.
select event_day, actor_type, action, event_count
from {{ source('phase6', 'v_phase6_audit_daily') }}
