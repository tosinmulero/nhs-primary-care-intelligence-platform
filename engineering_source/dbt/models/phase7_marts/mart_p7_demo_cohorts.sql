-- SIMULATION ONLY: non-identifiable aggregated reporting, not clinical decision support.
select code_system, concept_label, simulated_patient_count,
       suppressed_small_cell
from {{ source('phase6', 'v_phase6_demo_cohorts') }}
where suppressed_small_cell = false and simulated_patient_count >= 5
