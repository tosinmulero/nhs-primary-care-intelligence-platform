-- Synthetic aggregate-only staging model; source is a Phase 6 reporting view.
select * from {{ source('phase6', 'v_phase6_executive') }}
