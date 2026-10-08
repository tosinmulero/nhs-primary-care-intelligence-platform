-- SIMULATION ONLY: non-identifiable aggregated reporting, not clinical decision support.
select area_group, sex_at_registration, age_band, patient_count,
       round(100.0 * patient_count / nullif(sum(patient_count) over (),0),2) as pct_population
from {{ ref('stg_p7_demographics') }}
