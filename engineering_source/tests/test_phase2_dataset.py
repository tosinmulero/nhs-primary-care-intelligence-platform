"""Phase 2 acceptance tests; no real patient information is processed."""
import csv
from pathlib import Path

import pytest

from scripts.generate_synthetic_data import generate


@pytest.fixture(scope='module')
def build(tmp_path_factory):
    root = tmp_path_factory.mktemp('nhs_phase2')
    manifest = generate(root)
    return root, manifest


def read_rows(folder: Path, stem: str):
    with (folder / f'{stem}.csv').open(newline='', encoding='utf-8') as stream:
        return list(csv.DictReader(stream))


def test_expected_counts(build):
    _, manifest = build
    expected = {'patients': 8000, 'registrations': 8000, 'appointments': 20000,
                'hospital_correspondence': 3200, 'patient_summaries': 1500,
                'pathology_results': 4400, 'patient_conditions': 3600,
                'administrative_tasks': 6600, 'data_quality_issues': 300,
                'qof_demo_measurements': 36}
    for table, n in expected.items():
        assert manifest['tables'][table]['rows'] == n
    assert manifest['raw_intake']['rows'] == 8190


def test_relationships_and_synthetic_ids(build):
    root, _ = build
    base = root / 'data' / 'synthetic'
    patients = read_rows(base, 'patients')
    ids = {p['patient_id'] for p in patients}
    assert len(ids) == 8000
    assert all(p.startswith('PAT-') for p in ids)
    assert all(a['patient_id'] in ids for a in read_rows(base, 'appointments'))
    assert all(a['patient_id'] in ids for a in read_rows(base, 'pathology_results'))
    assert all(a['status'] == 'active' for a in read_rows(base, 'registrations'))


def test_reviewer_controls_and_demo_codes(build):
    root, _ = build
    base = root / 'data' / 'synthetic'
    staff = read_rows(base, 'staff')
    authorised = {s['staff_id'] for s in staff if s['role_code'] in
                  {'GP', 'NURSE_PRACTITIONER', 'PRACTICE_NURSE'}}
    for name, status_col, final in [
        ('patient_conditions', 'review_status', {'approved', 'rejected'}),
        ('hospital_correspondence', 'workflow_status', {'filed', 'rejected'}),
        ('patient_summaries', 'workflow_status', {'approved', 'rejected'}),
        ('pathology_results', 'review_status', {'reviewed', 'filed'}),
    ]:
        for row in read_rows(base, name):
            if row[status_col] in final:
                assert row['reviewed_by'] in authorised and row['reviewed_at']
            else:
                assert not row['reviewed_by'] and not row['reviewed_at']
    concepts = read_rows(base, 'terminology_concepts')
    assert {r['code_system'] for r in concepts} == {'DEMO_SNOMED', 'DEMO_READ'}
    assert all(r['is_synthetic'] == 'true' for r in concepts)


def test_raw_quality_defects_are_isolated(build):
    root, _ = build
    raw = read_rows(root / 'data' / 'raw', 'patients_intake')
    clean = read_rows(root / 'data' / 'synthetic', 'patients')
    assert len(raw) == 8190 and len(clean) == 8000
    assert sum(r['area_group'] == '' for r in raw) == 75
    assert sum(r['patient_id'].startswith('INVALID-') for r in raw) == 40
    assert len(raw) - len({r['patient_id'] for r in raw}) == 150


def test_deterministic_manifest(build):
    root, first = build
    second = generate(root)
    assert {key: item['sha256'] for key, item in first['tables'].items()} == {
        key: item['sha256'] for key, item in second['tables'].items()}
    assert first['raw_intake']['sha256'] == second['raw_intake']['sha256']