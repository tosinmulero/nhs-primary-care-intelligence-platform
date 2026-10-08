-- SIMULATION ONLY: non-identifiable aggregated reporting, not clinical decision support.
select 'condition_record'::text as record_type, code_system::text as sub_type,
       review_status::text as status, condition_records::integer as record_count
from {{ source('phase6', 'v_phase6_coding_status') }}
union all
select 'patient_summary'::text, 'new_registration'::text, workflow_status::text, summary_count::integer
from {{ source('phase6', 'v_phase6_patient_summary_status') }}
union all
select 'legacy_mapping'::text, 'read_to_snomed_demo'::text, status::text, mapping_count::integer
from {{ source('phase6', 'v_phase6_terminology_mapping_status') }}
