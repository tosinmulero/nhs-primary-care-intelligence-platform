"""Phase 6 offline integrity checks; database SQL is verified on the user's machine."""
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from phase6_reporting import SQL_RELATIVE_PATH, VIEW_METRICS, check_expected_views


def test_phase6_file_inventory():
    assert (ROOT/SQL_RELATIVE_PATH).is_file()
    assert (ROOT/'scripts/phase6_reporting.py').is_file()


def test_phase6_sql_inventory_and_safety():
    sql=(ROOT/SQL_RELATIVE_PATH).read_text(encoding='utf-8')
    check_expected_views(sql)
    assert len(VIEW_METRICS)==13


def test_phase6_coding_and_cohort_privacy():
    sql=(ROOT/SQL_RELATIVE_PATH).read_text(encoding='utf-8')
    assert "WHERE c.review_status='approved'" in sql
    assert 'raw_patient_count < 5' in sql
    assert 'SIMULATED - NOT OFFICIAL QOF' in sql


def test_phase6_reporter_has_database_guards():
    code=(ROOT/'scripts/phase6_reporting.py').read_text(encoding='utf-8')
    assert 'manifest_and_files(root)' in code
    assert 'check_database(cur, manifest, digest)' in code
    assert 'source_manifest_sha256' in code
    assert 'getpass.getpass' in code


def test_phase6_synthetic_data_still_present():
    import json
    manifest=json.loads((ROOT/'data/processed/phase2_manifest.json').read_text(encoding='utf-8'))
    assert manifest['tables']['patients']['rows']==8000
    assert manifest['tables']['appointments']['rows']==20000
    assert manifest['tables']['pathology_results']['rows']==4400
