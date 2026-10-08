-- Failing rows should be zero; synthetic data only.
select workstream, sum(record_count) as actual
from {{ ref('mart_p7_inbox_aging') }}
group by workstream
having (workstream='hospital_correspondence' and sum(record_count)<>3200)
    or (workstream='pathology' and sum(record_count)<>4400)
