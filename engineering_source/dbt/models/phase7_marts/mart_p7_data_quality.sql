-- SIMULATION ONLY: non-identifiable aggregated reporting, not clinical decision support.
select detected_date, rule_code, severity, issue_status, issue_count
from {{ source('phase6', 'v_phase6_quality_issue_trends') }}
