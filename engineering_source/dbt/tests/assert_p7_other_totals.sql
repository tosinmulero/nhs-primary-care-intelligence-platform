-- Failing rows should be zero; synthetic data only.
with a as (select coalesce(sum(appointment_count),0) as n from {{ ref('mart_p7_appointments') }}),
r as (select coalesce(sum(registrations),0) as n from {{ ref('mart_p7_registrations') }}),
q as (select coalesce(sum(issue_count),0) as n from {{ ref('mart_p7_data_quality') }}),
u as (select coalesce(sum(event_count),0) as n from {{ ref('mart_p7_audit_activity') }})
select * from a cross join r cross join q cross join u
where a.n<>20000 or r.n<>8000 or q.n<>300 or u.n<>7958
