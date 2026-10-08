"""Phase 5: synthetic clinical-administration review simulator.

Adds independent training queues and simulated audit events; does not alter core tables.
No actual NHS integrations, clinical sign-off or reliable identity verification.
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path


SQL_FILE = Path('sql/ddl/004_phase5_workflows.sql')
INITIAL_STATUS = {
    'CORRESPONDENCE': {'received': 'ADMIN_QUEUE', 'triage': 'ADMIN_QUEUE', 'pending_clinician': 'CLINICIAN_QUEUE'},
    'PATIENT_SUMMARY': {'draft': 'ADMIN_QUEUE', 'pending_clinician': 'CLINICIAN_QUEUE'},
    'PATHOLOGY': {'received': 'ADMIN_QUEUE', 'assigned': 'CLINICIAN_QUEUE'},
    'CONDITION_CODE': {'proposed': 'CLINICIAN_QUEUE'},
    'TERMINOLOGY_MAPPING': {'proposed': 'CLINICIAN_QUEUE'},
}
SOURCE_TABLES = {
    'CORRESPONDENCE': ('hospital_correspondence', 'correspondence_id', 'workflow_status', 'COR-'),
    'PATIENT_SUMMARY': ('patient_summaries', 'summary_id', 'workflow_status', 'SUM-'),
    'PATHOLOGY': ('pathology_results', 'result_id', 'review_status', 'PAT-'),
    'CONDITION_CODE': ('patient_conditions', 'condition_id', 'review_status', 'COD-'),
    'TERMINOLOGY_MAPPING': ('terminology_mappings', 'mapping_id', 'status', 'MAP-'),
}
CLINICIANS = {'GP','NURSE_PRACTITIONER','PRACTICE_NURSE'}
SUBMIT_ROLES = CLINICIANS | {'DATA_ADMIN','DATA_MANAGER'}


def simulated_transition(state: str, actor_role: str, action: str, reason: str = '') -> str:
    """Pure offline rule mirror; PostgreSQL independently enforces live transitions."""
    if action == 'SUBMIT' and state == 'ADMIN_QUEUE' and actor_role in SUBMIT_ROLES:
        return 'CLINICIAN_QUEUE'
    if state == 'CLINICIAN_QUEUE' and actor_role in CLINICIANS:
        if action == 'DEMO_APPROVE':
            return 'DEMO_APPROVED'
        if action == 'DEMO_REJECT' and len(reason.strip()) >= 8:
            return 'DEMO_REJECTED'
    raise ValueError('Transition not allowed for demo role, case state or reason')


def expected_counts_from_csv(root: Path) -> dict[str, int]:
    """Independently calculate open queue source counts from immutable synthetic CSVs."""
    import csv
    expected = {}
    for kind, (table, _, column, _) in SOURCE_TABLES.items():
        with (root / 'data' / 'synthetic' / (table + '.csv')).open(encoding='utf-8', newline='') as f:
            expected[kind] = sum(row[column] in INITIAL_STATUS[kind] for row in csv.DictReader(f))
    return expected


def assert_phase4(cur) -> None:
    cur.execute("SELECT to_regclass('audit.phase4_pipeline_runs')")
    if cur.fetchone()[0] is None:
        raise ValueError('Phase 4 audit table missing; run Phase 4 first')
    cur.execute('SELECT count(*) FROM audit.phase4_pipeline_runs')
    if cur.fetchone()[0] < 1:
        raise ValueError('Phase 4 transaction has not committed')
    cur.execute('SELECT accepted_rows,rejected_rows FROM analytics.v_phase4_quality_kpis')
    row = cur.fetchone()
    if row is None or (row[0],row[1]) != (8000,190):
        raise ValueError('Phase 4 acceptance reconciliation failed')


def validate_queues(cur, root: Path) -> dict:
    expected = expected_counts_from_csv(root)
    cur.execute('SELECT source_kind,count(*) FROM workflow.phase5_cases GROUP BY source_kind')
    actual = dict(cur.fetchall())
    if actual != expected:
        raise ValueError(f'Phase 5 queue reconciliation failed: {actual} != {expected}')
    # Verify every case ID corresponds to a pending source record, including original status and patient.
    for kind, (table, id_col, status_col, prefix) in SOURCE_TABLES.items():
        patient_check = 'c.patient_id IS NULL' if kind == 'TERMINOLOGY_MAPPING' else 'c.patient_id = s.patient_id'
        query = f"""
            SELECT count(*) FROM workflow.phase5_cases c
            LEFT JOIN core.{table} s ON s.{id_col}=c.source_record_id
            WHERE c.source_kind=%s
              AND (s.{id_col} IS NULL OR c.case_id <> %s || s.{id_col}
                   OR c.source_status <> s.{status_col}
                   OR NOT ({patient_check}))
        """
        cur.execute(query, (kind, prefix))
        if cur.fetchone()[0] != 0:
            raise ValueError(f'Phase 5 case-to-source integrity mismatch: {kind}')
    cur.execute('SELECT count(*) FROM workflow.phase5_cases c LEFT JOIN core.staff s ON s.staff_id=c.reviewed_by WHERE c.reviewed_by IS NOT NULL AND (s.role_code NOT IN (%s,%s,%s) OR s.staff_id IS NULL)',
                ('GP','NURSE_PRACTITIONER','PRACTICE_NURSE'))
    if cur.fetchone()[0]:
        raise ValueError('Phase 5 review assigned to non-clinician demonstration role')
    cur.execute('SELECT * FROM analytics.v_phase5_workflow_kpis')
    values = cur.fetchone()
    keys = [x.name for x in cur.description]
    result = dict(zip(keys,values))
    if result['total_demo_cases'] != sum(expected.values()):
        raise ValueError('Aggregate dashboard view is inconsistent with source queues')
    cur.execute('SELECT count(*) FROM audit.phase5_transition_events')
    result['demo_audit_events'] = cur.fetchone()[0]
    return {'classification': 'SIMULATION_ONLY', 'expected_source_queue_counts': expected, **result}


def _savepoint_probe(cur, statement: str, args: tuple, should_pass: bool) -> None:
    import psycopg
    cur.execute('SAVEPOINT phase5_probe')
    success = False
    try:
        cur.execute(statement, args)
        success = True
    except psycopg.Error:
        # Return transaction to healthy state before continuing.
        cur.execute('ROLLBACK TO SAVEPOINT phase5_probe')
    else:
        cur.execute('ROLLBACK TO SAVEPOINT phase5_probe')
    finally:
        cur.execute('RELEASE SAVEPOINT phase5_probe')
    if success != should_pass:
        raise ValueError(f'Unexpected workflow privilege test result: expected success={should_pass}')


def test_sql_roles(cur) -> None:
    cur.execute("SELECT staff_id FROM core.staff WHERE role_code='DATA_ADMIN' AND is_active LIMIT 1")
    admin = cur.fetchone()[0]
    cur.execute("SELECT staff_id FROM core.staff WHERE role_code='GP' AND is_active LIMIT 1")
    clinician = cur.fetchone()[0]
    cur.execute("SELECT case_id FROM workflow.phase5_cases WHERE case_status='ADMIN_QUEUE' ORDER BY case_id LIMIT 1")
    administrative_case = cur.fetchone()[0]
    cur.execute("SELECT case_id FROM workflow.phase5_cases WHERE case_status='CLINICIAN_QUEUE' ORDER BY case_id LIMIT 1")
    clinician_case = cur.fetchone()[0]
    stmt = 'SELECT workflow.apply_demo_transition(%s,%s,%s,%s)'
    _savepoint_probe(cur, stmt, (administrative_case,admin,'SUBMIT',None), True)
    _savepoint_probe(cur, stmt, (clinician_case,admin,'DEMO_APPROVE',None), False)
    _savepoint_probe(cur, stmt, (administrative_case,clinician,'DEMO_APPROVE',None), False)
    _savepoint_probe(cur, stmt, (clinician_case,clinician,'DEMO_APPROVE',None), True)
    _savepoint_probe(cur, stmt, (clinician_case,clinician,'DEMO_REJECT','Not enough evidence'), True)
    _savepoint_probe(cur, stmt, (clinician_case,clinician,'DEMO_REJECT',''), False)


def execute(root: Path, host: str, port: int, user: str, database: str,
            password: str, mode: str, case_id: str | None = None, staff_id: str | None = None,
            action: str | None = None, reason: str | None = None, limit: int = 12) -> dict:
    import psycopg
    from phase3_postgres import DBNAME, PROJECT, check_database, manifest_and_files
    if not DBNAME.fullmatch(database):
        raise ValueError('Refusing a non-project database name')
    manifest, digest = manifest_and_files(root)
    kwargs = dict(dbname=database,host=host,port=port,user=user,password=password,
                  connect_timeout=8, options='-c statement_timeout=120000')
    with psycopg.connect(**kwargs) as conn:
        with conn.cursor() as cur:
            check_database(cur,manifest,digest)
            assert_phase4(cur)
            if mode == 'install':
                cur.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (PROJECT + ':PHASE5',))
                cur.execute((root / SQL_FILE).read_text(encoding='utf-8'))
                report = validate_queues(cur,root)
                test_sql_roles(cur)  # savepoints ensure these tests leave no changes
                check_database(cur,manifest,digest)  # source records still untouched
            elif mode == 'queue':
                cur.execute('SELECT to_regclass(%s)', ('workflow.phase5_cases',))
                if cur.fetchone()[0] is None:
                    raise ValueError('Phase 5 is not installed')
                report = validate_queues(cur,root)
                cur.execute('SELECT case_id,source_kind,case_status FROM workflow.phase5_cases WHERE case_status IN (%s,%s) ORDER BY source_kind,case_id LIMIT %s',
                            ('ADMIN_QUEUE','CLINICIAN_QUEUE',max(1,min(limit,50))))
                report['preview_cases'] = [dict(zip(('case_id','source_kind','case_status'),row)) for row in cur.fetchall()]
            else:
                if not (case_id and staff_id and action):
                    raise ValueError('Transition needs --case-id --staff-id --decision')
                cur.execute('SELECT workflow.apply_demo_transition(%s,%s,%s,%s)',
                            (case_id,staff_id,action,reason))
                new_status = cur.fetchone()[0]
                report = validate_queues(cur,root)
                report['transition'] = {'case_id': case_id,'action': action,'new_status':new_status,
                                        'staff_id':staff_id}
    return report


def main() -> int:
    parser=argparse.ArgumentParser(description='NHS-style synthetic workflow training simulator')
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--host',default='localhost')
    parser.add_argument('--port',type=int,default=5432)
    parser.add_argument('--user',default='postgres')
    parser.add_argument('--database',default='nhs_primarycare_demo')
    parser.add_argument('--mode',choices=('install','queue','transition'),default='install')
    parser.add_argument('--case-id')
    parser.add_argument('--staff-id')
    parser.add_argument('--decision',choices=('SUBMIT','DEMO_APPROVE','DEMO_REJECT'))
    parser.add_argument('--reason',default='')
    parser.add_argument('--limit',type=int,default=12)
    opts=parser.parse_args()
    password=getpass.getpass('Local PostgreSQL password (not stored): ')
    try:
        report=execute(opts.root.resolve(),opts.host,opts.port,opts.user,opts.database,
                       password,opts.mode,opts.case_id,opts.staff_id,opts.decision,opts.reason,opts.limit)
    except Exception as exc:
        print(f'PHASE 5 FAILED: {type(exc).__name__}: {exc}',file=sys.stderr)
        return 1
    print('PHASE 5 PASS - synthetic queues and permission checks validated.' if opts.mode=='install'
          else 'PHASE 5 DEMO - simulation request completed.')
    print(json.dumps(report,indent=2,sort_keys=True))
    print('SIMULATION ONLY. No core medical records changed; no real clinical approval or identity verification.')
    return 0

if __name__=='__main__':
    raise SystemExit(main())
