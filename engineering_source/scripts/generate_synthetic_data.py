"""Phase 2: deterministic, entirely fictional primary-care administration data.

Not NHS-connected. Labels and mappings are fabricated demonstration concepts.
Never use these records or outputs for patient care.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from primarycare.contracts import DemoPatient

SEED = 20261008
AS_OF = date(2026, 10, 1)
N_PATIENTS = 8000
CLINICAL_ROLES = {'GP', 'NURSE_PRACTITIONER', 'PRACTICE_NURSE'}


def timestamp(day: date, hour: int = 9) -> str:
    return datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc).isoformat()


def choose_day(rng: random.Random, low: date, high: date) -> date:
    assert low <= high
    return low + timedelta(days=rng.randint(0, (high - low).days))


def write_table(root: Path, name: str, rows: list[dict], fields: list[str] | None = None) -> dict:
    if not rows:
        raise ValueError(f'{name} is empty')
    path = root / f'{name}.csv'
    columns = fields or list(rows[0].keys())
    root.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, lineterminator='\n', extrasaction='raise')
        writer.writeheader()
        writer.writerows(rows)
    return {'file': path.name, 'rows': len(rows), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def generate(root: Path, seed: int = SEED) -> dict:
    rng = random.Random(seed)
    data = root / 'data' / 'synthetic'
    raw = root / 'data' / 'raw'
    processed = root / 'data' / 'processed'
    processed.mkdir(parents=True, exist_ok=True)
    tables: dict[str, list[dict]] = {}

    # One active registration per fictional patient, and no NHS identifiers.
    patients = []
    regs = []
    for i in range(1, N_PATIENTS + 1):
        pid = f'PAT-{i:06d}'
        age = rng.choices([rng.randint(0, 17), rng.randint(18, 39), rng.randint(40, 64), rng.randint(65, 96)], [18, 30, 31, 21])[0]
        dob = AS_OF - timedelta(days=365 * age + rng.randint(0, 364))
        sex = rng.choices(['female', 'male', 'other', 'unknown'], [49, 49, 1, 1])[0]
        area = rng.choices(['urban', 'semi-rural', 'unknown'], [51, 47, 2])[0]
        patients.append({'patient_id': pid, 'date_of_birth': dob.isoformat(), 'sex_at_registration': sex,
                         'area_group': area})
        earliest = max(date(2000, 1, 1), dob)
        registered = choose_day(rng, earliest, AS_OF)
        regs.append({'registration_id': f'REG-{i:06d}', 'patient_id': pid,
                     'registered_on': registered.isoformat(), 'deregistered_on': '', 'status': 'active'})
    for row in patients:
        DemoPatient.model_validate(row)
    tables['patients'] = patients
    tables['registrations'] = regs

    staff = []
    role_counts = [('GP', 7), ('NURSE_PRACTITIONER', 2), ('PHYSICIAN_ASSOCIATE', 1),
                   ('PRACTICE_NURSE', 3), ('HCA', 4), ('DATA_ADMIN', 5), ('DATA_MANAGER', 1),
                   ('BUSINESS_MANAGER', 1), ('PHARMACIST', 2), ('OTHER', 2)]
    for role, count in role_counts:
        for _ in range(count):
            staff.append({'staff_id': f'STF-{len(staff)+1:04d}', 'role_code': role, 'is_active': 'true'})
    tables['staff'] = staff
    clinicians = [s['staff_id'] for s in staff if s['role_code'] in CLINICAL_ROLES]
    practitioners = [s['staff_id'] for s in staff if s['role_code'] in CLINICAL_ROLES | {'HCA', 'PHYSICIAN_ASSOCIATE'}]
    admins = [s['staff_id'] for s in staff if s['role_code'] in {'DATA_ADMIN', 'DATA_MANAGER'}]
    patient_ids = [p['patient_id'] for p in patients]

    appts = []
    for i in range(1, 20001):
        event_day = choose_day(rng, AS_OF - timedelta(days=540), AS_OF + timedelta(days=55))
        status = ('booked' if event_day > AS_OF else rng.choices(
            ['attended', 'cancelled', 'did_not_attend'], [81, 12, 7])[0])
        appts.append({'appointment_id': f'APT-{i:07d}', 'patient_id': rng.choice(patient_ids),
                      'staff_id': rng.choice(practitioners),
                      'scheduled_at': timestamp(event_day, rng.randint(8, 17)), 'status': status})
    tables['appointments'] = appts

    # All concepts and pairings below are invented; none are licensed SNOMED CT/Read mappings.
    labels = ['Blood pressure monitoring', 'Asthma care', 'Diabetes review', 'Medication review',
              'Vaccination history', 'Smoking status', 'Allergy review', 'Referral follow-up']
    concepts = []
    mappings = []
    for i, label in enumerate(labels, 1):
        old, modern = f'DEMO_READ-{i:03d}', f'DEMO_SNOMED-{i:03d}'
        concepts.extend([
            {'term_id': old, 'code_system': 'DEMO_READ', 'concept_label': f'{label} (invented legacy label)', 'is_synthetic': 'true'},
            {'term_id': modern, 'code_system': 'DEMO_SNOMED', 'concept_label': f'{label} (invented modern label)', 'is_synthetic': 'true'}])
        status = 'proposed' if i % 3 else 'demo_reviewed'
        mappings.append({'mapping_id': f'MAP-{i:04d}', 'legacy_term_id': old,
                         'modern_term_id': modern, 'status': status,
                         'reviewed_by': rng.choice(clinicians) if status != 'proposed' else '',
                         'reviewed_at': timestamp(AS_OF - timedelta(days=8)) if status != 'proposed' else ''})
    tables['terminology_concepts'] = concepts
    tables['terminology_mappings'] = mappings

    conditions = []
    for i in range(1, 3601):
        day = choose_day(rng, AS_OF - timedelta(days=760), AS_OF - timedelta(days=2))
        status = rng.choices(['approved', 'proposed', 'rejected'], [70, 25, 5])[0]
        conditions.append({'condition_id': f'CON-{i:07d}', 'patient_id': rng.choice(patient_ids),
                           'term_id': rng.choice([f'DEMO_SNOMED-{j:03d}' for j in range(1, 9)]),
                           'recorded_on': day.isoformat(), 'review_status': status,
                           'reviewed_by': rng.choice(clinicians) if status != 'proposed' else '',
                           'reviewed_at': timestamp(min(AS_OF, day + timedelta(days=1))) if status != 'proposed' else ''})
    tables['patient_conditions'] = conditions

    letters = []
    for i in range(1, 3201):
        day = choose_day(rng, AS_OF - timedelta(days=365), AS_OF - timedelta(days=4))
        status = rng.choices(['received', 'triage', 'pending_clinician', 'filed', 'rejected'], [9, 13, 22, 52, 4])[0]
        has_review = status in {'filed', 'rejected'}
        letters.append({'correspondence_id': f'LTR-{i:07d}', 'patient_id': rng.choice(patient_ids),
                        'received_at': timestamp(day),
                        'letter_category': rng.choice(['discharge', 'outpatient', 'referral', 'other']),
                        'workflow_status': status, 'assigned_staff_id': rng.choice(admins),
                        'reviewed_by': rng.choice(clinicians) if has_review else '',
                        'reviewed_at': timestamp(day + timedelta(days=2)) if has_review else ''})
    tables['hospital_correspondence'] = letters

    summaries = []
    for i in range(1, 1501):
        status = rng.choices(['draft', 'pending_clinician', 'approved', 'rejected'], [26, 23, 47, 4])[0]
        reviewed = status in {'approved', 'rejected'}
        summaries.append({'summary_id': f'SUM-{i:07d}', 'patient_id': rng.choice(patient_ids),
                          'summary_text': 'FICTIONAL training summary; clinical details intentionally excluded.',
                          'workflow_status': status, 'reviewed_by': rng.choice(clinicians) if reviewed else '',
                          'reviewed_at': timestamp(AS_OF - timedelta(days=rng.randint(1, 20))) if reviewed else ''})
    tables['patient_summaries'] = summaries

    pathology = []
    for i in range(1, 4401):
        day = choose_day(rng, AS_OF - timedelta(days=420), AS_OF - timedelta(days=4))
        status = rng.choices(['received', 'assigned', 'reviewed', 'filed'], [10, 28, 40, 22])[0]
        reviewed = status in {'reviewed', 'filed'}
        pathology.append({'result_id': f'LAB-{i:07d}', 'patient_id': rng.choice(patient_ids),
                          'received_at': timestamp(day),
                          'demo_test_label': rng.choice(['DEMO basic panel', 'DEMO monitoring panel', 'DEMO screening panel']),
                          'demo_result_text': 'SIMULATED result; no values or clinical interpretation',
                          'assigned_staff_id': rng.choice(clinicians) if status != 'received' else '',
                          'review_status': status, 'reviewed_by': rng.choice(clinicians) if reviewed else '',
                          'reviewed_at': timestamp(day + timedelta(days=2)) if reviewed else ''})
    tables['pathology_results'] = pathology

    tasks = []
    for i in range(1, 6601):
        day = choose_day(rng, AS_OF - timedelta(days=310), AS_OF - timedelta(days=7))
        status = rng.choices(['open', 'in_progress', 'on_hold', 'completed', 'cancelled'], [22, 19, 7, 47, 5])[0]
        due = day + timedelta(days=rng.randint(1, 21))
        tasks.append({'task_id': f'TSK-{i:07d}', 'patient_id': rng.choice(patient_ids),
                      'assigned_staff_id': rng.choice(admins),
                      'task_type': rng.choice(['record_summary', 'letter_coding', 'pathology_routing', 'quality_review', 'other']),
                      'created_at': timestamp(day), 'due_at': timestamp(due), 'task_status': status,
                      'completed_at': timestamp(day + timedelta(days=1)) if status == 'completed' else ''})
    tables['administrative_tasks'] = tasks

    # Three mock quality metrics with explicitly non-official definitions.
    defs = [('DEMO-QOF-01', 'Demonstration monitoring register', 'Approved demo blood pressure monitoring records',
             'Subset of qualifying mock records with an administrative check'),
            ('DEMO-QOF-02', 'Demonstration asthma review register', 'Approved demo asthma care records',
             'Subset with simulated review documentation'),
            ('DEMO-QOF-03', 'Demonstration diabetes review register', 'Approved demo diabetes review records',
             'Subset with simulated review documentation')]
    tables['qof_demo_indicators'] = [{'indicator_id': i, 'indicator_name': name,
                                     'denominator_definition': den, 'numerator_definition': num,
                                     'is_official_qof': 'false'} for i, name, den, num in defs]
    measurements = []
    for n in range(12):
        year, month = AS_OF.year, AS_OF.month - (11 - n)
        while month <= 0:
            year -= 1
            month += 12
        start = date(year, month, 1)
        for j, (iid, _, _, _) in enumerate(defs, 1):
            people = {c['patient_id'] for c in conditions if c['term_id'] == f'DEMO_SNOMED-{j:03d}'
                      and c['review_status'] == 'approved' and c['recorded_on'] < start.isoformat()}
            denom = len(people)
            ratio = rng.uniform(0.65, 0.86)
            measurements.append({'indicator_id': iid, 'measurement_month': start.isoformat(),
                                 'denominator_count': denom, 'numerator_count': int(denom * ratio)})
    tables['qof_demo_measurements'] = measurements

    # Raw intake intentionally contains noisy records; authoritative core CSVs remain clean.
    noisy = [dict(p) for p in patients]
    for i in range(75):
        noisy.append(dict(patients[i]))  # duplicate IDs
    for i in range(75, 150):
        v = dict(patients[i]); v['area_group'] = ''; noisy.append(v)
    for i in range(40):
        v = dict(patients[i+150]); v['patient_id'] = f'INVALID-{i:06d}'; noisy.append(v)

    issues = []
    for i in range(190):
        if i < 75:
            rule, key, severity = 'DUPLICATE_INTAKE_ID', patients[i]['patient_id'], 'high'
        elif i < 150:
            rule, key, severity = 'MISSING_AREA_INTAKE', patients[i]['patient_id'], 'medium'
        else:
            rule, key, severity = 'MALFORMED_INTAKE_ID', f'INVALID-{i-150:06d}', 'high'
        issues.append({'issue_id': f'DQI-{len(issues)+1:06d}', 'entity_name': 'raw.patients_intake',
                       'record_key': key, 'rule_code': rule, 'severity': severity,
                       'detected_at': timestamp(AS_OF), 'issue_status': 'open'})
    for i in range(110):
        issues.append({'issue_id': f'DQI-{len(issues)+1:06d}', 'entity_name': 'core.patient_conditions',
                       'record_key': conditions[i]['condition_id'], 'rule_code': 'DEMO_TERMINOLOGY_REVIEW',
                       'severity': 'low', 'detected_at': timestamp(AS_OF), 'issue_status': 'open'})
    tables['data_quality_issues'] = issues

    audits = []
    sources = [('patient_conditions', conditions, 'condition_id', 'review_status'),
               ('hospital_correspondence', letters, 'correspondence_id', 'workflow_status'),
               ('patient_summaries', summaries, 'summary_id', 'workflow_status'),
               ('pathology_results', pathology, 'result_id', 'review_status')]
    for entity, rows, id_col, status_col in sources:
        for obj in rows:
            if obj['reviewed_by']:
                audits.append({'event_id': f'EVT-{len(audits)+1:07d}', 'event_time': obj['reviewed_at'],
                               'actor_staff_id': obj['reviewed_by'], 'actor_type': 'SIMULATED_USER',
                               'action': f'DEMO_{obj[status_col].upper()}', 'entity_name': f'core.{entity}',
                               'entity_key': obj[id_col], 'detail': json.dumps({'simulation': True}, sort_keys=True)})
    audits.append({'event_id': f'EVT-{len(audits)+1:07d}', 'event_time': timestamp(AS_OF),
                   'actor_staff_id': '', 'actor_type': 'PIPELINE', 'action': 'SYNTHETIC_GENERATION',
                   'entity_name': 'data.synthetic', 'entity_key': f'SEED-{seed}',
                   'detail': json.dumps({'simulation': True, 'patient_count': N_PATIENTS}, sort_keys=True)})
    tables['audit_events'] = audits

    validate(tables, noisy)
    manifest = {'project': 'NHS Primary Care Intelligence | SIMULATION ONLY', 'seed': seed,
                'as_of': AS_OF.isoformat(), 'patient_target': N_PATIENTS,
                'notice': 'Invented data and terminology; not NHS-connected or clinically validated.',
                'tables': {}, 'raw_intake': {}}
    for name, rows in tables.items():
        manifest['tables'][name] = write_table(data, name, rows)
    manifest['raw_intake'] = write_table(raw, 'patients_intake', noisy)
    manifest_path = processed / 'phase2_manifest.json'
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print('PHASE 2 PASS | synthetic, internally validated')
    print(f'Patients: {len(patients):,} | Raw intake: {len(noisy):,} | Issues: {len(issues):,}')
    for table_name in ['appointments','patient_conditions','hospital_correspondence','patient_summaries',
                       'pathology_results','administrative_tasks','qof_demo_measurements','audit_events']:
        print(f'{table_name}: {len(tables[table_name]):,}')
    print(f'Manifest: {manifest_path}')
    return manifest


def validate(tables: dict[str, list[dict]], noisy: list[dict]) -> None:
    patients, regs, staff = tables['patients'], tables['registrations'], tables['staff']
    assert len(patients) == N_PATIENTS
    assert len(noisy) == N_PATIENTS + 190
    pids = {r['patient_id'] for r in patients}
    sids = {r['staff_id'] for r in staff}
    clinicians = {s['staff_id'] for s in staff if s['role_code'] in CLINICAL_ROLES}
    assert len(pids) == N_PATIENTS and all(re.fullmatch(r'PAT-\d{6}', x) for x in pids)
    assert all(r['status'] == 'active' and not r['deregistered_on'] and r['patient_id'] in pids for r in regs)
    assert len(regs) == N_PATIENTS and len({r['patient_id'] for r in regs}) == N_PATIENTS
    for table, key in [('appointments','appointment_id'),('registrations','registration_id'),
                       ('staff','staff_id'),('patient_conditions','condition_id'),
                       ('hospital_correspondence','correspondence_id'),('patient_summaries','summary_id'),
                       ('pathology_results','result_id'),('administrative_tasks','task_id'),
                       ('data_quality_issues','issue_id'),('audit_events','event_id')]:
        assert len({r[key] for r in tables[table]}) == len(tables[table]), f'duplicate {table} IDs'
    for name in ['appointments','patient_conditions','hospital_correspondence', 'patient_summaries',
                 'pathology_results', 'administrative_tasks']:
        assert all(row['patient_id'] in pids for row in tables[name]), f'{name} missing patient'
    for name in ['appointments','hospital_correspondence','pathology_results','administrative_tasks']:
        assert all((not row.get('assigned_staff_id') or row['assigned_staff_id'] in sids) and
                   (not row.get('staff_id') or row['staff_id'] in sids) for row in tables[name])
    for name, status_col, final in [('patient_conditions','review_status', {'approved','rejected'}),
                                    ('hospital_correspondence','workflow_status', {'filed','rejected'}),
                                    ('patient_summaries','workflow_status', {'approved','rejected'}),
                                    ('pathology_results','review_status', {'reviewed','filed'})]:
        for row in tables[name]:
            final_state = row[status_col] in final
            assert bool(row['reviewed_by']) == final_state and bool(row['reviewed_at']) == final_state
            if final_state:
                assert row['reviewed_by'] in clinicians, f'unauthorised reviewer in {name}'
    assert all(row['code_system'] in ('DEMO_READ','DEMO_SNOMED') and row['is_synthetic'] == 'true'
               for row in tables['terminology_concepts'])
    assert all(row['numerator_count'] <= row['denominator_count'] for row in tables['qof_demo_measurements'])
    assert all(row['due_at'] >= row['created_at'] and bool(row['completed_at']) == (row['task_status'] == 'completed')
               for row in tables['administrative_tasks'])
    assert len(tables['data_quality_issues']) == 300
    intake_df = pd.DataFrame(noisy)
    assert int(intake_df.duplicated(subset=['patient_id']).sum()) == 150
    assert int(intake_df['area_group'].eq('').sum()) == 75
    assert int((~intake_df['patient_id'].str.fullmatch(r'PAT-\d{6}')).sum()) == 40
    forbidden_fields = {'nhs_number', 'first_name', 'last_name', 'full_name', 'email', 'phone', 'postcode', 'address'}
    assert all(not (forbidden_fields & set(row)) for records in tables.values() for row in records)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--seed', type=int, default=SEED)
    args = parser.parse_args()
    generate(args.root.resolve(), args.seed)