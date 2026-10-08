"""Phase 3 checks that run in CI without a PostgreSQL service."""
from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]


def test_phase3_loader_parses_and_has_safe_database_name():
    path = ROOT / 'scripts' / 'phase3_postgres.py'
    ast.parse(path.read_text(encoding='utf-8'))
    code = path.read_text(encoding='utf-8')
    assert 'DBNAME.fullmatch(database)' in code
    assert 'manifest_and_files(root)' in code
    assert 'copy_csv(' in code


def test_phase3_sql_has_staging_and_operational_views():
    sql = (ROOT / 'sql' / 'ddl' / '002_phase3_auxiliary.sql').read_text(encoding='utf-8')
    for required in ('raw.patients_intake', 'staging.v_patient_intake_quality',
                     'staging.v_clean_patient_intake', 'analytics.v_intake_quality_summary',
                     'analytics.v_practice_overview', 'analytics.v_workflow_backlog',
                     'audit.portfolio_metadata', 'audit.enforce_demo_reviewer'):
        assert required in sql


def test_phase3_sql_does_not_claim_official_nhs_metrics():
    sql = (ROOT / 'sql' / 'ddl' / '002_phase3_auxiliary.sql').read_text(encoding='utf-8').lower()
    assert 'all data fictional' in sql
    assert 'not an authentication/authorisation system' in sql
