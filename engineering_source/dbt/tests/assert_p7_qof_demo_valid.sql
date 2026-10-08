-- Failing rows should be zero; synthetic data only.
select * from {{ ref('mart_p7_qof_demo') }}
where numerator_count > denominator_count or numerator_count < 0 or denominator_count < 0
   or demo_percent > 100 or demo_percent < 0
   or indicator_classification <> 'SIMULATED - NOT OFFICIAL QOF'
