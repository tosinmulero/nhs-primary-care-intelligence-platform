-- SIMULATION ONLY: non-identifiable aggregated reporting, not clinical decision support.
select measurement_month, indicator_id, indicator_name,
       denominator_count, numerator_count, demo_percent, indicator_classification
from {{ source('phase6', 'v_phase6_qof_demo_trends') }}
