-- PHASE 3: add synthetic raw intake, staging data-quality views and operational analytics.
-- All data fictional. Never use for patient care or to represent actual NHS/QOF performance.
CREATE TABLE raw.patients_intake (
    intake_row_number bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    patient_id text,
    date_of_birth text,
    sex_at_registration text,
    area_group text
);

CREATE VIEW staging.v_patient_intake_quality AS
SELECT r.*,
       (patient_id IS NULL OR patient_id !~ '^PAT-[0-9]{6}$') AS is_malformed_id,
       (area_group IS NULL OR btrim(area_group) = '') AS is_missing_area,
       (row_number() OVER (PARTITION BY patient_id ORDER BY intake_row_number) > 1) AS is_duplicate_id
FROM raw.patients_intake AS r;

CREATE VIEW staging.v_clean_patient_intake AS
SELECT patient_id, date_of_birth::date AS date_of_birth,
       sex_at_registration, area_group
FROM staging.v_patient_intake_quality
WHERE NOT (is_malformed_id OR is_missing_area OR is_duplicate_id);

CREATE VIEW analytics.v_intake_quality_summary AS
SELECT count(*)::integer AS intake_records,
       count(*) FILTER (WHERE is_malformed_id)::integer AS malformed_ids,
       count(*) FILTER (WHERE is_duplicate_id)::integer AS duplicate_ids,
       count(*) FILTER (WHERE is_missing_area)::integer AS missing_area
FROM staging.v_patient_intake_quality;

CREATE VIEW analytics.v_practice_overview AS
SELECT
  (SELECT active_patients FROM analytics.v_active_patient_count) AS registered_patients,
  (SELECT count(*)::integer FROM core.appointments) AS appointments_total,
  (SELECT count(*)::integer FROM core.hospital_correspondence
     WHERE workflow_status IN ('received', 'triage', 'pending_clinician')) AS correspondence_backlog,
  (SELECT count(*)::integer FROM core.pathology_results
     WHERE review_status IN ('received', 'assigned')) AS pathology_pending_review,
  (SELECT count(*)::integer FROM core.administrative_tasks
     WHERE task_status IN ('open', 'in_progress', 'on_hold')) AS tasks_outstanding,
  (SELECT count(*)::integer FROM core.data_quality_issues WHERE issue_status='open') AS open_data_quality_issues;

CREATE VIEW analytics.v_workflow_backlog AS
SELECT 'Hospital correspondence'::text AS workflow, workflow_status AS status, count(*)::integer AS item_count
  FROM core.hospital_correspondence GROUP BY workflow_status
UNION ALL
SELECT 'Pathology results', review_status, count(*)::integer
  FROM core.pathology_results GROUP BY review_status
UNION ALL
SELECT 'Administrative tasks', task_status, count(*)::integer
  FROM core.administrative_tasks GROUP BY task_status;

CREATE TABLE audit.portfolio_metadata (
    project_key text PRIMARY KEY,
    manifest_sha256 char(64) NOT NULL CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    dataset_seed bigint NOT NULL,
    reporting_date date NOT NULL,
    deployed_at timestamptz NOT NULL DEFAULT now()
);

-- Demonstration role matrix only: this is NOT an authentication/authorisation system.
CREATE TABLE audit.demo_permission_matrix (
    demo_role text NOT NULL CHECK (demo_role IN ('DATA_ADMIN','DATA_MANAGER','CLINICIAN','REPORTING_READER')),
    demo_action text NOT NULL,
    permitted boolean NOT NULL,
    PRIMARY KEY(demo_role, demo_action)
);
INSERT INTO audit.demo_permission_matrix(demo_role, demo_action, permitted) VALUES
('DATA_ADMIN', 'QUEUE_AND_DRAFT', true),
('DATA_ADMIN', 'CLINICAL_APPROVAL', false),
('DATA_MANAGER', 'QUEUE_AND_DRAFT', true),
('DATA_MANAGER', 'CLINICAL_APPROVAL', false),
('CLINICIAN', 'QUEUE_AND_DRAFT', true),
('CLINICIAN', 'CLINICAL_APPROVAL', true),
('REPORTING_READER', 'QUEUE_AND_DRAFT', false),
('REPORTING_READER', 'CLINICAL_APPROVAL', false);

-- Server-side check of simulated clinician-review metadata; not real professional approval.
CREATE FUNCTION audit.enforce_demo_reviewer() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    workflow_status text;
    reviewer_id text;
    review_time text;
    reviewer_role text;
    is_final boolean;
BEGIN
    workflow_status := to_jsonb(NEW)->>TG_ARGV[0];
    reviewer_id := to_jsonb(NEW)->>'reviewed_by';
    review_time := to_jsonb(NEW)->>'reviewed_at';
    is_final := workflow_status = ANY(string_to_array(TG_ARGV[1], ','));
    IF is_final AND (reviewer_id IS NULL OR review_time IS NULL) THEN
        RAISE EXCEPTION 'Finalised mock clinical record must have simulated reviewer and timestamp';
    END IF;
    IF NOT is_final AND (reviewer_id IS NOT NULL OR review_time IS NOT NULL) THEN
        RAISE EXCEPTION 'Pending mock clinical record cannot carry final reviewer metadata';
    END IF;
    IF reviewer_id IS NOT NULL THEN
        SELECT role_code INTO reviewer_role FROM core.staff WHERE staff_id = reviewer_id;
        IF reviewer_role IS NULL OR reviewer_role NOT IN ('GP','NURSE_PRACTITIONER','PRACTICE_NURSE') THEN
            RAISE EXCEPTION 'Only simulated clinical reviewer roles may finalise records';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_condition_reviewer BEFORE INSERT OR UPDATE ON core.patient_conditions
    FOR EACH ROW EXECUTE FUNCTION audit.enforce_demo_reviewer('review_status', 'approved,rejected');
CREATE TRIGGER trg_letter_reviewer BEFORE INSERT OR UPDATE ON core.hospital_correspondence
    FOR EACH ROW EXECUTE FUNCTION audit.enforce_demo_reviewer('workflow_status', 'filed,rejected');
CREATE TRIGGER trg_summary_reviewer BEFORE INSERT OR UPDATE ON core.patient_summaries
    FOR EACH ROW EXECUTE FUNCTION audit.enforce_demo_reviewer('workflow_status', 'approved,rejected');
CREATE TRIGGER trg_pathology_reviewer BEFORE INSERT OR UPDATE ON core.pathology_results
    FOR EACH ROW EXECUTE FUNCTION audit.enforce_demo_reviewer('review_status', 'reviewed,filed');
