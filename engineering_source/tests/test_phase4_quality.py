"""Local tests for Phase 4: no database or clinical access required."""
import csv
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def classify_rows():
    raw = ROOT / 'data/raw/patients_intake.csv'
    counts = Counter()
    accepted = set()
    seen = set()
    with raw.open(encoding='utf-8-sig', newline='') as f:
        for row in csv.DictReader(f):
            counts['raw'] += 1
            patient_id = row['patient_id']
            malformed = not bool(re.fullmatch(r'PAT-[0-9]{6}', patient_id or ''))
            missing = not bool((row['area_group'] or '').strip())
            duplicate = patient_id in seen
            seen.add(patient_id)
            if malformed:
                counts['malformed'] += 1
            if missing:
                counts['missing'] += 1
            if duplicate:
                counts['duplicate'] += 1
            if malformed or missing or duplicate:
                counts['rejected'] += 1
            else:
                counts['accepted'] += 1
                accepted.add(patient_id)
    return counts, accepted


def test_phase4_manifest_checksum():
    manifest = json.loads((ROOT / 'data/processed/phase2_manifest.json').read_text(encoding='utf-8'))
    filename = ROOT / 'data/raw/patients_intake.csv'
    assert hashlib.sha256(filename.read_bytes()).hexdigest() == manifest['raw_intake']['sha256']


def test_phase4_offline_classification():
    counts, _ = classify_rows()
    assert (counts['raw'], counts['accepted'], counts['rejected']) == (8190, 8000, 190)
    assert (counts['duplicate'], counts['missing'], counts['malformed']) == (150, 75, 40)


def test_phase4_accepted_matches_authoritative_patients():
    _, accepted = classify_rows()
    with (ROOT / 'data/synthetic/patients.csv').open(encoding='utf-8-sig', newline='') as f:
        patients = {row['patient_id'] for row in csv.DictReader(f)}
    assert len(accepted) == len(patients) == 8000
    assert accepted == patients


def test_phase4_sql_is_scoped_and_analytics_present():
    ddl = (ROOT / 'sql/ddl/003_phase4_quality.sql').read_text(encoding='utf-8')
    for name in ('staging.phase4_patient_intake_curated', 'audit.phase4_pipeline_runs',
                 'analytics.v_phase4_quality_kpis', 'analytics.v_phase4_quality_issue_counts'):
        assert name in ddl
    assert 'DROP TABLE' not in ddl.upper()
    assert 'TRUNCATE' not in ddl.upper()


def test_phase4_python_persistent_report_is_simulated():
    code = (ROOT / 'scripts/phase4_quality.py').read_text(encoding='utf-8')
    assert 'SIMULATION_ONLY' in code
    assert 'check_database(cur, manifest, digest)' in code
    assert 'getpass.getpass' in code
