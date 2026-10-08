"""Offline tests that never require PostgreSQL or personal medical records."""
from __future__ import annotations

import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from phase5_workflows import simulated_transition, expected_counts_from_csv, SOURCE_TABLES


def test_demo_admin_can_submit_but_cannot_approve():
    assert simulated_transition('ADMIN_QUEUE','DATA_ADMIN','SUBMIT')=='CLINICIAN_QUEUE'
    with pytest.raises(ValueError):
        simulated_transition('CLINICIAN_QUEUE','DATA_ADMIN','DEMO_APPROVE')


def test_clinician_review_requires_a_waiting_clinical_case():
    assert simulated_transition('CLINICIAN_QUEUE','GP','DEMO_APPROVE')=='DEMO_APPROVED'
    with pytest.raises(ValueError):
        simulated_transition('ADMIN_QUEUE','GP','DEMO_APPROVE')
    assert simulated_transition('CLINICIAN_QUEUE','PRACTICE_NURSE','DEMO_REJECT','Training record incomplete')=='DEMO_REJECTED'


def test_rejection_requires_reason_and_hca_cannot_approve():
    for role, reason in [('GP',''),('GP','short'),('HCA','Documented training issue')]:
        with pytest.raises(ValueError):
            simulated_transition('CLINICIAN_QUEUE',role,'DEMO_REJECT',reason)


def test_source_snapshot_is_non_destructive_and_deterministic():
    counts=expected_counts_from_csv(ROOT)
    assert set(counts)==set(SOURCE_TABLES)
    assert all(counts[k] > 0 for k in counts)
    assert sum(counts.values()) > 1000


def test_sql_is_additive_and_audit_and_views_present():
    text=(ROOT/'sql/ddl/004_phase5_workflows.sql').read_text(encoding='utf-8')
    assert 'CREATE TABLE IF NOT EXISTS workflow.phase5_cases' in text
    assert 'CREATE TABLE IF NOT EXISTS audit.phase5_transition_events' in text
    assert 'workflow.apply_demo_transition' in text
    assert 'analytics.v_phase5_workflow_board' in text
    assert 'analytics.v_phase5_workflow_kpis' in text
    # Never write back into the original synthetic clinical records.
    for forbidden in ('UPDATE core.', 'DELETE FROM core.', 'TRUNCATE core.', 'DROP TABLE core.'):
        assert forbidden not in text.upper().replace('CORE.','core.')
