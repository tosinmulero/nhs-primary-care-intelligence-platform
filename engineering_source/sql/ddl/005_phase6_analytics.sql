-- NHS Primary Care Portfolio Phase 6 â€” ADDITIVE ANALYTICS (SIMULATION ONLY).
-- Entirely synthetic, non-clinical, aggregate-only reporting. No real NHS data.
-- Clinical cohorts are *illustrations* and not validated diagnoses or medical advice.

CREATE TABLE IF NOT EXISTS audit.phase6_reporting_deployments (
    sql_sha256 char(64) PRIMARY KEY CHECK (sql_sha256 ~ '^[0-9a-f]{64}$'),
    source_manifest_sha256 char(64) NOT NULL CHECK (source_manifest_sha256 ~ '^[0-9a-f]{64}$'),
    installed_at timestamptz NOT NULL DEFAULT now(),
    classification text NOT NULL DEFAULT 'SIMULATION_ONLY' CHECK (classification='SIMULATION_ONLY')
);

-- Executive-level totals; acceptance rate is NOT a clinical care quality score.
CREATE OR REPLACE VIEW analytics.v_phase6_executive AS
SELECT q.synthetic_as_of AS reporting_date,
       p.registered_patients, p.appointments_total,
       p.correspondence_backlog, p.pathology_pending_review,
       p.tasks_outstanding, p.open_data_quality_issues,
       q.intake_rows, q.accepted_rows, q.rejected_rows,
       q.intake_acceptance_pct,
       w.total_demo_cases, w.awaiting_admin, w.awaiting_clinician,
       w.demo_approved, w.demo_rejected
FROM analytics.v_practice_overview p
CROSS JOIN analytics.v_phase4_quality_kpis q
CROSS JOIN analytics.v_phase5_workflow_kpis w;

CREATE OR REPLACE VIEW analytics.v_phase6_population_demographics AS
WITH age_groups AS (
 SELECT p.area_group, p.sex_at_registration,
        CASE WHEN date_part('year', age(m.reporting_date,p.date_of_birth)) < 18 THEN '00-17'
             WHEN date_part('year', age(m.reporting_date,p.date_of_birth)) < 35 THEN '18-34'
             WHEN date_part('year', age(m.reporting_date,p.date_of_birth)) < 50 THEN '35-49'
             WHEN date_part('year', age(m.reporting_date,p.date_of_birth)) < 65 THEN '50-64'
             ELSE '65+' END AS age_band
 FROM core.patients p
 JOIN core.registrations r ON p.patient_id=r.patient_id AND r.status='active'
 CROSS JOIN audit.portfolio_metadata m
 WHERE m.project_key='NHS Primary Care Intelligence | SIMULATION ONLY'
)
SELECT area_group, sex_at_registration, age_band,
       count(*)::integer AS patient_count
FROM age_groups
GROUP BY area_group,sex_at_registration,age_band;

CREATE OR REPLACE VIEW analytics.v_phase6_registration_monthly AS
SELECT date_trunc('month', registered_on)::date AS registration_month,
       status, count(*)::integer AS registrations
FROM core.registrations
GROUP BY 1,2;

CREATE OR REPLACE VIEW analytics.v_phase6_appointments_monthly AS
SELECT date_trunc('month', scheduled_at AT TIME ZONE 'UTC')::date AS appointment_month,
       status, count(*)::integer AS appointment_count
FROM core.appointments
GROUP BY 1,2;

-- Maps coding completeness only to recorded administrative status; NOT clinical correctness.
CREATE OR REPLACE VIEW analytics.v_phase6_coding_status AS
SELECT t.code_system, c.review_status, count(*)::integer AS condition_records
FROM core.patient_conditions c JOIN core.terminology_concepts t ON t.term_id=c.term_id
GROUP BY t.code_system,c.review_status;

CREATE OR REPLACE VIEW analytics.v_phase6_patient_summary_status AS
SELECT workflow_status, count(*)::integer AS summary_count
FROM core.patient_summaries GROUP BY workflow_status;

CREATE OR REPLACE VIEW analytics.v_phase6_terminology_mapping_status AS
SELECT status, count(*)::integer AS mapping_count
FROM core.terminology_mappings
GROUP BY status;

CREATE OR REPLACE VIEW analytics.v_phase6_correspondence_queue AS
WITH queue AS (
 SELECT c.letter_category,c.workflow_status,
        CASE WHEN c.received_at::date > m.reporting_date THEN 'Future synthetic date'
             WHEN m.reporting_date-c.received_at::date <= 7 THEN '0-7 days'
             WHEN m.reporting_date-c.received_at::date <= 14 THEN '8-14 days'
             ELSE '15+ days' END AS illustrative_age_band
 FROM core.hospital_correspondence c
 CROSS JOIN audit.portfolio_metadata m
 WHERE m.project_key='NHS Primary Care Intelligence | SIMULATION ONLY'
)
SELECT letter_category,workflow_status,illustrative_age_band,
       count(*)::integer AS correspondence_count
FROM queue GROUP BY 1,2,3;

CREATE OR REPLACE VIEW analytics.v_phase6_pathology_queue AS
WITH queue AS (
 SELECT p.review_status,
        CASE WHEN p.received_at::date > m.reporting_date THEN 'Future synthetic date'
             WHEN m.reporting_date-p.received_at::date <= 7 THEN '0-7 days'
             WHEN m.reporting_date-p.received_at::date <= 14 THEN '8-14 days'
             ELSE '15+ days' END AS illustrative_age_band
 FROM core.pathology_results p
 CROSS JOIN audit.portfolio_metadata m
 WHERE m.project_key='NHS Primary Care Intelligence | SIMULATION ONLY'
)
SELECT review_status,illustrative_age_band,count(*)::integer AS result_count
FROM queue GROUP BY 1,2;

CREATE OR REPLACE VIEW analytics.v_phase6_task_performance AS
SELECT t.task_type, t.task_status,
       CASE WHEN t.task_status IN ('completed','cancelled') THEN 'Closed'
            WHEN t.due_at IS NULL THEN 'No due date'
            WHEN t.due_at::date < m.reporting_date THEN 'Overdue (demo)'
            ELSE 'Not overdue (demo)' END AS due_bucket,
       count(*)::integer AS task_count
FROM core.administrative_tasks t
CROSS JOIN audit.portfolio_metadata m
WHERE m.project_key='NHS Primary Care Intelligence | SIMULATION ONLY'
GROUP BY 1,2,3;

-- Deliberately only *approved synthetic condition records*. No individual identifiers.
-- Suppress small (<5) cohort counts even though input is fictitious.
CREATE OR REPLACE VIEW analytics.v_phase6_demo_cohorts AS
WITH aggregates AS (
 SELECT t.term_id, t.code_system, t.concept_label,
        count(DISTINCT c.patient_id)::integer AS raw_patient_count
 FROM core.patient_conditions c JOIN core.terminology_concepts t ON t.term_id=c.term_id
 WHERE c.review_status='approved'
 GROUP BY 1,2,3
)
SELECT code_system,concept_label,
       CASE WHEN raw_patient_count < 5 THEN NULL::integer ELSE raw_patient_count END AS simulated_patient_count,
       raw_patient_count < 5 AS suppressed_small_cell
FROM aggregates;

CREATE OR REPLACE VIEW analytics.v_phase6_qof_demo_trends AS
SELECT m.measurement_month,i.indicator_id,i.indicator_name,
       m.denominator_count,m.numerator_count,
       round(100.0*m.numerator_count/nullif(m.denominator_count,0),2) AS demo_percent,
       'SIMULATED - NOT OFFICIAL QOF'::text AS indicator_classification
FROM core.qof_demo_measurements m
JOIN core.qof_demo_indicators i ON i.indicator_id=m.indicator_id;

CREATE OR REPLACE VIEW analytics.v_phase6_quality_issue_trends AS
SELECT detected_at::date AS detected_date,rule_code,severity,issue_status,
       count(*)::integer AS issue_count
FROM core.data_quality_issues
GROUP BY 1,2,3,4;

CREATE OR REPLACE VIEW analytics.v_phase6_workflow_queue AS
SELECT source_kind,case_status,count(*)::integer AS case_count
FROM workflow.phase5_cases GROUP BY 1,2;

CREATE OR REPLACE VIEW analytics.v_phase6_audit_daily AS
SELECT event_time::date AS event_day,actor_type,action,count(*)::integer AS event_count
FROM audit.events GROUP BY 1,2,3;
