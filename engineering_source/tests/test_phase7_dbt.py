"""Phase 7 source and safety checks that run without database access."""
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from phase7_verify import MARTS,STAGES,SOURCE_TABLES,EXPECTED_SUMS,model_inventory,project_sha256


def test_phase7_expected_inventory():
    model_inventory(ROOT)
    assert len(MARTS)==12 and len(STAGES)==4 and len(SOURCE_TABLES)==15


def test_phase7_marts_only_aggregate_fields():
    for name in MARTS:
        code=(ROOT/f'dbt/models/phase7_marts/{name}.sql').read_text(encoding='utf-8').lower()
        assert '{{' in code and 'patient_id' not in code
        assert 'nhs_number' not in code


def test_phase7_reconciliation_coverage():
    assert len(EXPECTED_SUMS)==9
    assert EXPECTED_SUMS['mart_p7_demographics']==('patient_count',8000)
    assert EXPECTED_SUMS['mart_p7_workflow_queues']==('case_count',4749)


def test_phase7_no_clinical_decisions_and_suppression():
    cohorts=(ROOT/'dbt/models/phase7_marts/mart_p7_demo_cohorts.sql').read_text(encoding='utf-8')
    assert 'simulated_patient_count >= 5' in cohorts
    assert 'suppressed_small_cell = false' in cohorts
    assert 'SIMULATION ONLY' in (ROOT/'docs/PHASE7_DBT.md').read_text(encoding='utf-8')


def test_phase7_models_have_dbt_tests():
    yml=(ROOT/'dbt/models/phase7_models.yml').read_text(encoding='utf-8')
    for name in MARTS: assert f'  - name: {name}' in yml
    assert yml.count('          - not_null')>=30
    assert len(list((ROOT/'dbt/tests').glob('assert_p7_*.sql')))==8


def test_phase7_model_checksum_stable():
    assert len(project_sha256(ROOT))==64
