-- SIMULATION ONLY: non-identifiable aggregated reporting, not clinical decision support.
select 'hospital_correspondence'::text as workstream, letter_category::text as record_category,
       workflow_status::text as status, illustrative_age_band::text as age_band,
       correspondence_count::integer as record_count
from {{ ref('stg_p7_correspondence') }}
union all
select 'pathology'::text, 'lab_result'::text, review_status::text,
       illustrative_age_band::text, result_count::integer
from {{ ref('stg_p7_pathology') }}
