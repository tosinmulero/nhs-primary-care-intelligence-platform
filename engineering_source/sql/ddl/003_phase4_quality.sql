-- NHS Primary Care Intelligence | Phase 4 | SYNTHETIC DATA ONLY.
-- Additive migration; never alters raw or core records.
CREATE TABLE IF NOT EXISTS staging.phase4_patient_intake_curated (
    patient_id text PRIMARY KEY CHECK (patient_id ~ '^PAT-[0-9]{6}$'),
    date_of_birth date NOT NULL,
    sex_at_registration text NOT NULL,
    area_group text NOT NULL,
    source_row bigint NOT NULL UNIQUE,
    source_manifest_sha256 char(64) NOT NULL
);

CREATE TABLE IF NOT EXISTS audit.phase4_pipeline_runs (
    run_key char(64) PRIMARY KEY CHECK (run_key ~ '^[0-9a-f]{64}$'),
    source_manifest_sha256 char(64) NOT NULL,
    pipeline_version text NOT NULL,
    processed_at timestamptz NOT NULL DEFAULT now(),
    report_json jsonb NOT NULL,
    CHECK (report_json->>'classification' = 'SIMULATION_ONLY')
);

CREATE OR REPLACE VIEW staging.v_phase4_patient_intake_decisions AS
SELECT intake_row_number, patient_id, date_of_birth, sex_at_registration, area_group,
       is_malformed_id, is_missing_area, is_duplicate_id,
       CASE WHEN (is_malformed_id OR is_missing_area OR is_duplicate_id)
         THEN 'REJECT' ELSE 'ACCEPT' END AS intake_decision,
       concat_ws(', ',
           CASE WHEN is_malformed_id THEN 'MALFORMED_PATIENT_ID' END,
           CASE WHEN is_missing_area THEN 'MISSING_AREA_GROUP' END,
           CASE WHEN is_duplicate_id THEN 'DUPLICATE_PATIENT_ID' END
       ) AS quality_reasons
FROM staging.v_patient_intake_quality;

CREATE OR REPLACE VIEW analytics.v_phase4_quality_issue_counts AS
SELECT 'MALFORMED_PATIENT_ID'::text AS rule_code, count(*)::integer AS affected_rows
FROM staging.v_phase4_patient_intake_decisions WHERE is_malformed_id
UNION ALL
SELECT 'MISSING_AREA_GROUP', count(*)::integer
FROM staging.v_phase4_patient_intake_decisions WHERE is_missing_area
UNION ALL
SELECT 'DUPLICATE_PATIENT_ID', count(*)::integer
FROM staging.v_phase4_patient_intake_decisions WHERE is_duplicate_id;

CREATE OR REPLACE VIEW analytics.v_phase4_intake_status AS
SELECT intake_decision, count(*)::integer AS record_count
FROM staging.v_phase4_patient_intake_decisions
GROUP BY intake_decision;

-- Quality score is the RAW INTAKE ACCEPTANCE RATE, not an NHS quality-of-care score.
CREATE OR REPLACE VIEW analytics.v_phase4_quality_kpis AS
WITH intake AS (
 SELECT count(*)::integer AS intake_rows,
        count(*) FILTER (WHERE intake_decision = 'ACCEPT')::integer AS accepted_rows,
        count(*) FILTER (WHERE intake_decision = 'REJECT')::integer AS rejected_rows
 FROM staging.v_phase4_patient_intake_decisions
),
registry AS (
 SELECT count(*)::integer AS active_registrations FROM core.registrations WHERE status = 'active'
),
issues AS (
 SELECT count(*) FILTER (WHERE issue_status = 'open')::integer AS outstanding_quality_issues
 FROM core.data_quality_issues
),
queue AS (
 SELECT count(*) FILTER (WHERE task_status IN ('open','in_progress','on_hold'))::integer AS outstanding_tasks
 FROM core.administrative_tasks
)
SELECT m.reporting_date AS synthetic_as_of,
       i.intake_rows, i.accepted_rows, i.rejected_rows,
       round(100.0 * i.accepted_rows / nullif(i.intake_rows,0), 2) AS intake_acceptance_pct,
       (SELECT count(*)::integer FROM staging.phase4_patient_intake_curated) AS curated_patients,
       r.active_registrations, q.outstanding_quality_issues, t.outstanding_tasks
FROM intake i CROSS JOIN registry r CROSS JOIN issues q CROSS JOIN queue t
CROSS JOIN audit.portfolio_metadata m
WHERE m.project_key = 'NHS Primary Care Intelligence | SIMULATION ONLY';

-- Does not contain individual patient identifiers: dashboard-safe synthetic aggregates.
CREATE OR REPLACE VIEW analytics.v_phase4_dashboard_quality_summary AS
SELECT 'Raw intake'::text AS measure, intake_rows AS measure_value FROM analytics.v_phase4_quality_kpis
UNION ALL SELECT 'Accepted intake', accepted_rows FROM analytics.v_phase4_quality_kpis
UNION ALL SELECT 'Rejected intake', rejected_rows FROM analytics.v_phase4_quality_kpis
UNION ALL SELECT 'Active registrations', active_registrations FROM analytics.v_phase4_quality_kpis
UNION ALL SELECT 'Outstanding quality issues', outstanding_quality_issues FROM analytics.v_phase4_quality_kpis
UNION ALL SELECT 'Outstanding admin tasks', outstanding_tasks FROM analytics.v_phase4_quality_kpis;
