-- Failing rows should be zero; synthetic data only.
select sum(patient_count) as total_patients
from {{ ref('mart_p7_demographics') }}
having sum(patient_count) <> 8000
