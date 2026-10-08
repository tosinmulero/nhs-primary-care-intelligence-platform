-- Failing rows should be zero; synthetic data only.
select * from {{ ref('mart_p7_demo_cohorts') }}
where suppressed_small_cell = true or simulated_patient_count < 5 or simulated_patient_count is null
