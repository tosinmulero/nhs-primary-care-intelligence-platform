-- PHASE 1 DDL CONTRACT ONLY: do not execute until Phase 3 review.
-- Entirely synthetic, not connected to any NHS patient or clinical system.
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS analytics;
CREATE SCHEMA IF NOT EXISTS audit;

CREATE TABLE IF NOT EXISTS core.patients (
    patient_id text PRIMARY KEY CHECK (patient_id ~ '^PAT-[0-9]{6}$'),
    date_of_birth date NOT NULL,
    sex_at_registration text NOT NULL CHECK (sex_at_registration IN ('female','male','other','unknown')),
    area_group text NOT NULL CHECK (area_group IN ('urban','semi-rural','unknown')),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS core.staff (
    staff_id text PRIMARY KEY CHECK (staff_id ~ '^STF-[0-9]{4}$'),
    role_code text NOT NULL CHECK (role_code IN (
        'GP','NURSE_PRACTITIONER','PRACTICE_NURSE','PHYSICIAN_ASSOCIATE',
        'HCA','DATA_ADMIN','DATA_MANAGER','BUSINESS_MANAGER','PHARMACIST','OTHER'
    )),
    is_active boolean NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS core.registrations (
    registration_id text PRIMARY KEY,
    patient_id text NOT NULL REFERENCES core.patients(patient_id),
    registered_on date NOT NULL,
    deregistered_on date,
    status text NOT NULL CHECK (status IN ('active','closed')),
    CHECK (deregistered_on IS NULL OR deregistered_on >= registered_on),
    CHECK ((status = 'active' AND deregistered_on IS NULL) OR (status = 'closed' AND deregistered_on IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_one_active_registration
    ON core.registrations(patient_id) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS core.appointments (
    appointment_id text PRIMARY KEY,
    patient_id text NOT NULL REFERENCES core.patients(patient_id),
    staff_id text NOT NULL REFERENCES core.staff(staff_id),
    scheduled_at timestamptz NOT NULL,
    status text NOT NULL CHECK (status IN ('booked','attended','cancelled','did_not_attend'))
);

CREATE TABLE IF NOT EXISTS core.terminology_concepts (
    term_id text PRIMARY KEY,
    code_system text NOT NULL CHECK (code_system IN ('DEMO_READ','DEMO_SNOMED')),
    concept_label text NOT NULL,
    is_synthetic boolean NOT NULL DEFAULT true CHECK (is_synthetic = true)
);

CREATE TABLE IF NOT EXISTS core.terminology_mappings (
    mapping_id text PRIMARY KEY,
    legacy_term_id text NOT NULL REFERENCES core.terminology_concepts(term_id),
    modern_term_id text NOT NULL REFERENCES core.terminology_concepts(term_id),
    status text NOT NULL CHECK (status IN ('proposed','demo_reviewed','rejected')),
    reviewed_by text REFERENCES core.staff(staff_id),
    reviewed_at timestamptz,
    CHECK ((status = 'proposed' AND reviewed_by IS NULL AND reviewed_at IS NULL)
        OR (status <> 'proposed' AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)),
    UNIQUE (legacy_term_id, modern_term_id)
);

CREATE TABLE IF NOT EXISTS core.patient_conditions (
    condition_id text PRIMARY KEY,
    patient_id text NOT NULL REFERENCES core.patients(patient_id),
    term_id text NOT NULL REFERENCES core.terminology_concepts(term_id),
    recorded_on date NOT NULL,
    review_status text NOT NULL CHECK (review_status IN ('proposed','approved','rejected')),
    reviewed_by text REFERENCES core.staff(staff_id),
    reviewed_at timestamptz,
    CHECK ((review_status = 'proposed' AND reviewed_by IS NULL AND reviewed_at IS NULL)
        OR (review_status <> 'proposed' AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS core.hospital_correspondence (
    correspondence_id text PRIMARY KEY,
    patient_id text NOT NULL REFERENCES core.patients(patient_id),
    received_at timestamptz NOT NULL,
    letter_category text NOT NULL CHECK (letter_category IN ('discharge','outpatient','referral','other')),
    workflow_status text NOT NULL CHECK (workflow_status IN ('received','triage','pending_clinician','filed','rejected')),
    assigned_staff_id text REFERENCES core.staff(staff_id),
    reviewed_by text REFERENCES core.staff(staff_id),
    reviewed_at timestamptz,
    CHECK ((workflow_status NOT IN ('filed','rejected'))
        OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS core.patient_summaries (
    summary_id text PRIMARY KEY,
    patient_id text NOT NULL REFERENCES core.patients(patient_id),
    summary_text text NOT NULL,
    workflow_status text NOT NULL CHECK (workflow_status IN ('draft','pending_clinician','approved','rejected')),
    reviewed_by text REFERENCES core.staff(staff_id),
    reviewed_at timestamptz,
    CHECK ((workflow_status NOT IN ('approved','rejected'))
        OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS core.pathology_results (
    result_id text PRIMARY KEY,
    patient_id text NOT NULL REFERENCES core.patients(patient_id),
    received_at timestamptz NOT NULL,
    demo_test_label text NOT NULL,
    demo_result_text text,
    assigned_staff_id text REFERENCES core.staff(staff_id),
    review_status text NOT NULL CHECK (review_status IN ('received','assigned','reviewed','filed')),
    reviewed_by text REFERENCES core.staff(staff_id),
    reviewed_at timestamptz,
    CHECK ((review_status NOT IN ('reviewed','filed'))
        OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS core.administrative_tasks (
    task_id text PRIMARY KEY,
    patient_id text REFERENCES core.patients(patient_id),
    assigned_staff_id text REFERENCES core.staff(staff_id),
    task_type text NOT NULL CHECK (task_type IN ('record_summary','letter_coding','pathology_routing','quality_review','other')),
    created_at timestamptz NOT NULL,
    due_at timestamptz,
    task_status text NOT NULL CHECK (task_status IN ('open','in_progress','on_hold','completed','cancelled')),
    completed_at timestamptz,
    CHECK (due_at IS NULL OR due_at >= created_at),
    CHECK ((task_status = 'completed' AND completed_at IS NOT NULL)
        OR (task_status <> 'completed' AND completed_at IS NULL))
);

CREATE TABLE IF NOT EXISTS core.data_quality_issues (
    issue_id text PRIMARY KEY,
    entity_name text NOT NULL,
    record_key text NOT NULL,
    rule_code text NOT NULL,
    severity text NOT NULL CHECK (severity IN ('low','medium','high')),
    detected_at timestamptz NOT NULL,
    issue_status text NOT NULL CHECK (issue_status IN ('open','resolved','accepted_risk'))
);

CREATE TABLE IF NOT EXISTS core.qof_demo_indicators (
    indicator_id text PRIMARY KEY,
    indicator_name text NOT NULL,
    denominator_definition text NOT NULL,
    numerator_definition text NOT NULL,
    is_official_qof boolean NOT NULL DEFAULT false CHECK (is_official_qof = false)
);

CREATE TABLE IF NOT EXISTS core.qof_demo_measurements (
    indicator_id text NOT NULL REFERENCES core.qof_demo_indicators(indicator_id),
    measurement_month date NOT NULL,
    denominator_count integer NOT NULL CHECK (denominator_count >= 0),
    numerator_count integer NOT NULL CHECK (numerator_count >= 0),
    PRIMARY KEY (indicator_id, measurement_month),
    CHECK (numerator_count <= denominator_count),
    CHECK (EXTRACT(DAY FROM measurement_month) = 1)
);

CREATE TABLE IF NOT EXISTS audit.events (
    event_id text PRIMARY KEY,
    event_time timestamptz NOT NULL DEFAULT now(),
    actor_staff_id text REFERENCES core.staff(staff_id),
    actor_type text NOT NULL CHECK (actor_type IN ('SIMULATED_USER','PIPELINE')),
    action text NOT NULL,
    entity_name text NOT NULL,
    entity_key text NOT NULL,
    detail jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS ix_appt_patient_date ON core.appointments(patient_id, scheduled_at);
CREATE INDEX IF NOT EXISTS ix_correspondence_status ON core.hospital_correspondence(workflow_status, received_at);
CREATE INDEX IF NOT EXISTS ix_pathology_status ON core.pathology_results(review_status, received_at);
CREATE INDEX IF NOT EXISTS ix_tasks_status_due ON core.administrative_tasks(task_status, due_at);
CREATE INDEX IF NOT EXISTS ix_quality_issue_status ON core.data_quality_issues(issue_status, detected_at);
CREATE INDEX IF NOT EXISTS ix_audit_event_time ON audit.events(event_time);

CREATE OR REPLACE VIEW analytics.v_active_patient_count AS
SELECT count(DISTINCT patient_id)::integer AS active_patients
FROM core.registrations
WHERE status = 'active';