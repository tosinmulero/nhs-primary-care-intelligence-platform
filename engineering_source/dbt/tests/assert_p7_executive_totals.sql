-- Failing rows should be zero; synthetic data only.
select * from {{ ref('mart_p7_executive') }}
where registered_patients <> 8000 or appointments_total <> 20000
   or intake_rows <> 8190 or accepted_rows <> 8000 or rejected_rows <> 190
   or total_demo_cases <> 4749 or (awaiting_admin + awaiting_clinician + demo_approved + demo_rejected) <> 4749
