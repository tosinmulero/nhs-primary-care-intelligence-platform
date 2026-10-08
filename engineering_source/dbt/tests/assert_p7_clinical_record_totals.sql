-- Failing rows should be zero; synthetic data only.
select record_type, sum(record_count) as actual
from {{ ref('mart_p7_record_quality') }}
group by record_type
having (record_type='condition_record' and sum(record_count)<>3600)
    or (record_type='patient_summary' and sum(record_count)<>1500)
    or (record_type='legacy_mapping' and sum(record_count)<>8)
