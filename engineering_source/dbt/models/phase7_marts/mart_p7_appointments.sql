-- SIMULATION ONLY: non-identifiable aggregated reporting, not clinical decision support.
select appointment_month, status, appointment_count
from {{ source('phase6', 'v_phase6_appointments_monthly') }}
