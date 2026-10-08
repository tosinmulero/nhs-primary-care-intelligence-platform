-- SIMULATION ONLY: non-identifiable aggregated reporting, not clinical decision support.
select registration_month, status, registrations
from {{ source('phase6', 'v_phase6_registration_monthly') }}
