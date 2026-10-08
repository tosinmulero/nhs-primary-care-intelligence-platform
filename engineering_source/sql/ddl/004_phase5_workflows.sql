-- Phase 5 | SIMULATION ONLY | ADDITIVE migration, no changes to core clinical records.
-- These are training case reviews, not actual clinical authorisations or NHS integrations.
CREATE SCHEMA IF NOT EXISTS workflow;

CREATE TABLE IF NOT EXISTS workflow.phase5_cases (
    case_id text PRIMARY KEY,
    source_kind text NOT NULL CHECK (source_kind IN
        ('CORRESPONDENCE','PATIENT_SUMMARY','PATHOLOGY','CONDITION_CODE','TERMINOLOGY_MAPPING')),
    source_record_id text NOT NULL,
    patient_id text REFERENCES core.patients(patient_id),
    source_status text NOT NULL,
    case_status text NOT NULL CHECK (case_status IN
        ('ADMIN_QUEUE','CLINICIAN_QUEUE','DEMO_APPROVED','DEMO_REJECTED')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    reviewed_by text REFERENCES core.staff(staff_id),
    reviewed_at timestamptz,
    UNIQUE (source_kind, source_record_id),
    CONSTRAINT phase5_demo_reviewer_chk CHECK (
        (case_status IN ('ADMIN_QUEUE','CLINICIAN_QUEUE') AND reviewed_by IS NULL AND reviewed_at IS NULL)
        OR (case_status IN ('DEMO_APPROVED','DEMO_REJECTED') AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)
    ),
    CONSTRAINT phase5_patient_ref_chk CHECK
        ((source_kind='TERMINOLOGY_MAPPING' AND patient_id IS NULL)
         OR (source_kind<>'TERMINOLOGY_MAPPING' AND patient_id IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS ix_phase5_queue_status ON workflow.phase5_cases(source_kind,case_status);
CREATE TABLE IF NOT EXISTS audit.phase5_transition_events (
    event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    case_id text NOT NULL REFERENCES workflow.phase5_cases(case_id),
    actor_staff_id text NOT NULL REFERENCES core.staff(staff_id),
    actor_demo_role text NOT NULL,
    action_code text NOT NULL CHECK (action_code IN ('SUBMIT','DEMO_APPROVE','DEMO_REJECT')),
    from_status text NOT NULL,
    to_status text NOT NULL,
    reason text,
    action_at timestamptz NOT NULL DEFAULT now(),
    classification text NOT NULL DEFAULT 'SIMULATION_ONLY'
        CHECK (classification='SIMULATION_ONLY')
);
CREATE INDEX IF NOT EXISTS ix_phase5_audit_case ON audit.phase5_transition_events(case_id,event_id);

-- Reviewer role control is a DEMONSTRATION ONLY; PostgreSQL login is not proof of a clinician's identity.
-- Caller is provided a fictional staff identifier. No production authorisation claims are made.
CREATE OR REPLACE FUNCTION workflow.apply_demo_transition(
    p_case_id text,
    p_actor_staff_id text,
    p_action text,
    p_reason text DEFAULT NULL
) RETURNS text LANGUAGE plpgsql AS $$
DECLARE
    v_case workflow.phase5_cases%ROWTYPE;
    v_role text;
    v_active boolean;
    v_next text;
    v_reason text := nullif(btrim(coalesce(p_reason,'')), '');
BEGIN
    SELECT * INTO v_case FROM workflow.phase5_cases
    WHERE case_id=p_case_id FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'Unrecognised demonstration case ID'; END IF;
    SELECT role_code, is_active INTO v_role, v_active FROM core.staff
    WHERE staff_id=p_actor_staff_id;
    IF v_role IS NULL OR NOT v_active THEN
        RAISE EXCEPTION 'Active fictitious staff ID required';
    END IF;
    IF length(coalesce(v_reason,'')) > 500 THEN
        RAISE EXCEPTION 'Demonstration reason must be 500 characters or fewer';
    END IF;
    IF p_action='SUBMIT' AND v_case.case_status='ADMIN_QUEUE'
        AND v_role IN ('DATA_ADMIN','DATA_MANAGER','GP','NURSE_PRACTITIONER','PRACTICE_NURSE') THEN
        v_next := 'CLINICIAN_QUEUE';
    ELSIF p_action IN ('DEMO_APPROVE','DEMO_REJECT')
        AND v_case.case_status='CLINICIAN_QUEUE'
        AND v_role IN ('GP','NURSE_PRACTITIONER','PRACTICE_NURSE') THEN
        IF p_action='DEMO_REJECT' AND length(coalesce(v_reason,'')) < 8 THEN
            RAISE EXCEPTION 'Simulated rejection requires a meaningful reason (8+ characters)';
        END IF;
        v_next := CASE WHEN p_action='DEMO_APPROVE' THEN 'DEMO_APPROVED' ELSE 'DEMO_REJECTED' END;
    ELSE
        RAISE EXCEPTION 'Demo role / workflow state does not permit this transition';
    END IF;
    UPDATE workflow.phase5_cases
       SET case_status=v_next, updated_at=now(),
           reviewed_by=CASE WHEN v_next IN ('DEMO_APPROVED','DEMO_REJECTED') THEN p_actor_staff_id ELSE NULL END,
           reviewed_at=CASE WHEN v_next IN ('DEMO_APPROVED','DEMO_REJECTED') THEN now() ELSE NULL END
     WHERE case_id=p_case_id;
    INSERT INTO audit.phase5_transition_events
       (case_id,actor_staff_id,actor_demo_role,action_code,from_status,to_status,reason)
    VALUES (p_case_id,p_actor_staff_id,v_role,p_action,v_case.case_status,v_next,v_reason);
    RETURN v_next;
END;
$$;

-- Seed only unfinished synthetic records. Immutable authoritative source tables are unchanged.
INSERT INTO workflow.phase5_cases
(case_id,source_kind,source_record_id,patient_id,source_status,case_status)
SELECT 'COR-'||correspondence_id,'CORRESPONDENCE',correspondence_id,patient_id,workflow_status,
       CASE WHEN workflow_status='pending_clinician' THEN 'CLINICIAN_QUEUE' ELSE 'ADMIN_QUEUE' END
FROM core.hospital_correspondence
WHERE workflow_status IN ('received','triage','pending_clinician')
ON CONFLICT (case_id) DO NOTHING;

INSERT INTO workflow.phase5_cases
(case_id,source_kind,source_record_id,patient_id,source_status,case_status)
SELECT 'SUM-'||summary_id,'PATIENT_SUMMARY',summary_id,patient_id,workflow_status,
       CASE WHEN workflow_status='pending_clinician' THEN 'CLINICIAN_QUEUE' ELSE 'ADMIN_QUEUE' END
FROM core.patient_summaries
WHERE workflow_status IN ('draft','pending_clinician')
ON CONFLICT (case_id) DO NOTHING;

INSERT INTO workflow.phase5_cases
(case_id,source_kind,source_record_id,patient_id,source_status,case_status)
SELECT 'PAT-'||result_id,'PATHOLOGY',result_id,patient_id,review_status,
       CASE WHEN review_status='assigned' THEN 'CLINICIAN_QUEUE' ELSE 'ADMIN_QUEUE' END
FROM core.pathology_results
WHERE review_status IN ('received','assigned')
ON CONFLICT (case_id) DO NOTHING;

INSERT INTO workflow.phase5_cases
(case_id,source_kind,source_record_id,patient_id,source_status,case_status)
SELECT 'COD-'||condition_id,'CONDITION_CODE',condition_id,patient_id,review_status,'CLINICIAN_QUEUE'
FROM core.patient_conditions WHERE review_status='proposed'
ON CONFLICT (case_id) DO NOTHING;

INSERT INTO workflow.phase5_cases
(case_id,source_kind,source_record_id,patient_id,source_status,case_status)
SELECT 'MAP-'||mapping_id,'TERMINOLOGY_MAPPING',mapping_id,NULL,status,'CLINICIAN_QUEUE'
FROM core.terminology_mappings WHERE status='proposed'
ON CONFLICT (case_id) DO NOTHING;

-- Dashboard-safe aggregated metrics: no personal identifiers or raw clinical content.
CREATE OR REPLACE VIEW analytics.v_phase5_workflow_board AS
SELECT source_kind,case_status,count(*)::integer AS case_count
FROM workflow.phase5_cases GROUP BY source_kind,case_status;

CREATE OR REPLACE VIEW analytics.v_phase5_workflow_kpis AS
SELECT count(*)::integer AS total_demo_cases,
       count(*) FILTER (WHERE case_status='ADMIN_QUEUE')::integer AS awaiting_admin,
       count(*) FILTER (WHERE case_status='CLINICIAN_QUEUE')::integer AS awaiting_clinician,
       count(*) FILTER (WHERE case_status='DEMO_APPROVED')::integer AS demo_approved,
       count(*) FILTER (WHERE case_status='DEMO_REJECTED')::integer AS demo_rejected
FROM workflow.phase5_cases;

CREATE OR REPLACE VIEW analytics.v_phase5_transition_activity AS
SELECT e.action_at::date AS action_date, c.source_kind,e.action_code,count(*)::integer AS action_count
FROM audit.phase5_transition_events e JOIN workflow.phase5_cases c USING(case_id)
GROUP BY e.action_at::date,c.source_kind,e.action_code;

-- Reporting-date age only, not a clinical SLA or patient-safety prioritisation algorithm.
CREATE OR REPLACE VIEW analytics.v_phase5_correspondence_age_demo AS
SELECT c.case_status,
       CASE WHEN (m.reporting_date - h.received_at::date) > 14 THEN 'Over 14 days'
            WHEN (m.reporting_date - h.received_at::date) > 7 THEN '8-14 days'
            ELSE '0-7 days' END AS illustrative_age_band,
       count(*)::integer AS case_count
FROM workflow.phase5_cases c
JOIN core.hospital_correspondence h ON c.source_kind='CORRESPONDENCE' AND c.source_record_id=h.correspondence_id
JOIN audit.portfolio_metadata m ON m.project_key='NHS Primary Care Intelligence | SIMULATION ONLY'
WHERE c.case_status IN ('ADMIN_QUEUE','CLINICIAN_QUEUE')
GROUP BY c.case_status,illustrative_age_band;
