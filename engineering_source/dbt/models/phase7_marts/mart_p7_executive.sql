-- SIMULATION ONLY: non-identifiable aggregated reporting, not clinical decision support.
select reporting_date, registered_patients, appointments_total,
       correspondence_backlog, pathology_pending_review, tasks_outstanding,
       open_data_quality_issues, intake_rows, accepted_rows, rejected_rows,
       intake_acceptance_pct, total_demo_cases, awaiting_admin, awaiting_clinician,
       demo_approved, demo_rejected,
       round(100.0 * (awaiting_admin + awaiting_clinician) / nullif(total_demo_cases,0),2) as pct_awaiting_review
from {{ ref('stg_p7_executive') }}
