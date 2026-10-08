"""Safely check the synthetic NHS dbt reporting deployment; no real NHS records."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path

MARTS = {
    'mart_p7_executive', 'mart_p7_demographics', 'mart_p7_registrations',
    'mart_p7_appointments', 'mart_p7_record_quality', 'mart_p7_inbox_aging',
    'mart_p7_admin_tasks', 'mart_p7_workflow_queues', 'mart_p7_qof_demo',
    'mart_p7_data_quality', 'mart_p7_audit_activity', 'mart_p7_demo_cohorts',
}
STAGES = {
    'stg_p7_executive', 'stg_p7_demographics', 'stg_p7_correspondence',
    'stg_p7_pathology',
}
BI_SCHEMA = 'nhs_bi_marts'
SOURCE_TABLES = {
    'v_phase6_executive', 'v_phase6_population_demographics',
    'v_phase6_registration_monthly', 'v_phase6_appointments_monthly',
    'v_phase6_coding_status', 'v_phase6_patient_summary_status',
    'v_phase6_terminology_mapping_status', 'v_phase6_correspondence_queue',
    'v_phase6_pathology_queue', 'v_phase6_task_performance',
    'v_phase6_demo_cohorts', 'v_phase6_qof_demo_trends',
    'v_phase6_quality_issue_trends', 'v_phase6_workflow_queue',
    'v_phase6_audit_daily',
}
EXPECTED_SUMS = {
    'mart_p7_demographics': ('patient_count', 8000),
    'mart_p7_registrations': ('registrations', 8000),
    'mart_p7_appointments': ('appointment_count', 20000),
    'mart_p7_inbox_aging': ('record_count', 7600),
    'mart_p7_admin_tasks': ('task_count', 6600),
    'mart_p7_workflow_queues': ('case_count', 4749),
    'mart_p7_qof_demo': (None, 36),
    'mart_p7_data_quality': ('issue_count', 300),
    'mart_p7_audit_activity': ('event_count', 7958),
}


def project_sha256(root: Path) -> str:
    files = sorted((root / 'dbt' / 'models').rglob('*.sql'))
    files += sorted((root / 'dbt' / 'models').rglob('*.yml'))
    files += sorted((root / 'dbt' / 'tests').rglob('assert_p7_*.sql'))
    assert files, 'Phase 7 files missing'
    hasher = hashlib.sha256()
    for path in files:
        hasher.update(path.relative_to(root).as_posix().encode())
        hasher.update(path.read_bytes())
    return hasher.hexdigest()


def model_inventory(root: Path) -> None:
    stage_names = {x.stem for x in (root/'dbt/models/phase7_staging').glob('stg_p7_*.sql')}
    mart_names = {x.stem for x in (root/'dbt/models/phase7_marts').glob('mart_p7_*.sql')}
    if stage_names != STAGES or mart_names != MARTS:
        raise ValueError(f'Unexpected model inventory; stages={stage_names ^ STAGES}, marts={mart_names ^ MARTS}')
    for path in (root/'dbt/models').rglob('*.sql'):
        text = path.read_text(encoding='utf-8').lower()
        for forbidden in ('patient_id', 'nhs_number', 'date_of_birth', 'summary_text', 'demo_result_text'):
            if forbidden in text:
                raise ValueError(f'Identifier/text field prohibited in dbt model: {path}: {forbidden}')
    source = (root/'dbt/models/phase7_sources.yml').read_text(encoding='utf-8')
    if not all(('name: '+name) in source for name in SOURCE_TABLES):
        raise ValueError('Incomplete Phase 6 source declarations')


def verify(cur, phase: str, root: Path, manifest: dict, digest: str) -> dict:
    from phase3_postgres import check_database
    check_database(cur, manifest, digest)
    cur.execute("SELECT to_regclass('audit.phase6_reporting_deployments')")
    if cur.fetchone()[0] is None:
        raise ValueError('Phase 6 SQL reporting layer has not been installed')
    cur.execute("SELECT count(*) FROM audit.phase6_reporting_deployments WHERE source_manifest_sha256=%s", (digest,))
    if cur.fetchone()[0] < 1:
        raise ValueError('Phase 6 source manifest does not match')
    cur.execute("SELECT table_name FROM information_schema.views WHERE table_schema='analytics' AND table_name LIKE 'v_phase6_%'")
    got_sources = {x[0] for x in cur.fetchall()}
    if not SOURCE_TABLES.issubset(got_sources):
        raise ValueError(f'Phase 6 analytics source view missing: {SOURCE_TABLES - got_sources}')

    cur.execute("""SELECT c.relname FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=%s AND c.relkind IN ('r','v','m','p','f')""", (BI_SCHEMA,))
    existing = {row[0] for row in cur.fetchall()}
    allowed = MARTS | STAGES
    unknown = existing - allowed
    if unknown:
        raise ValueError(f'Existing reporting schema contains unrelated objects, aborting: {sorted(unknown)}')
    if phase == 'preflight':
        # Only exact Phase 7-prefixed model names are permitted in this dedicated database.
        # Allow partial reruns if an earlier dbt build created some, but not all, views.
        return {'preflight': 'PASS', 'existing_phase7_model_count': len(existing)}

    if existing != allowed:
        raise ValueError(f'Dbt relation inventory mismatch: missing={sorted(allowed-existing)}')
    forbidden = {'patient_id','nhs_number','date_of_birth','summary_text','demo_result_text'}
    cur.execute('SELECT column_name FROM information_schema.columns WHERE table_schema=%s', (BI_SCHEMA,))
    exposed = {x[0] for x in cur.fetchall()} & forbidden
    if exposed:
        raise ValueError(f'Personal or clinical free-text fields exposed to reporting: {sorted(exposed)}')
    metrics = {}
    for name, (column, expected) in EXPECTED_SUMS.items():
        expression = 'count(*)' if column is None else f'coalesce(sum({column}),0)'
        # All model/table/column names are fixed developer-owned allowlisted constants.
        cur.execute(f'SELECT {expression} FROM {BI_SCHEMA}.{name}')
        n = int(cur.fetchone()[0])
        if n != expected:
            raise ValueError(f'Failed reconciliation for {name}: {n} != {expected}')
        metrics[name] = n
    cur.execute(f'SELECT registered_patients, intake_rows, accepted_rows, rejected_rows, total_demo_cases FROM {BI_SCHEMA}.mart_p7_executive')
    executive = cur.fetchall()
    if len(executive) != 1 or tuple(executive[0]) != (8000,8190,8000,190,4749):
        raise ValueError(f'Dbt executive model reconciliation failed: {executive}')
    cur.execute(f'SELECT count(*) FROM {BI_SCHEMA}.mart_p7_demo_cohorts WHERE suppressed_small_cell OR simulated_patient_count < 5 OR simulated_patient_count IS NULL')
    if cur.fetchone()[0]:
        raise ValueError('Dbt model includes suppressed or small cohorts')
    cur.execute(f'SELECT count(*) FROM {BI_SCHEMA}.mart_p7_record_quality')
    record_status_rows = int(cur.fetchone()[0])
    if record_status_rows < 1:
        raise ValueError('Clinical record quality mart is empty')
    return {
        'phase': 'PHASE 7 PASS', 'classification': 'SIMULATION_ONLY',
        'database_scope': 'AGGREGATE_ONLY_NO_PATIENT_IDENTIFIERS',
        'reporting_schema': BI_SCHEMA, 'mart_count': len(MARTS),
        'staging_model_count': len(STAGES), 'source_view_count': len(SOURCE_TABLES),
        'executive': {'patients':8000,'raw_intake':8190,'accepted_intake':8000,'rejected_intake':190,'workflow_cases':4749},
        'reconciliations': metrics, 'source_manifest_sha256': digest,
        'phase7_model_sha256': project_sha256(root),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('preflight','verify'), required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--host', default='localhost')
    parser.add_argument('--port', type=int, default=5432)
    parser.add_argument('--user', default='postgres')
    parser.add_argument('--database', default='nhs_primarycare_demo')
    args = parser.parse_args()
    if args.database != 'nhs_primarycare_demo':
        raise SystemExit('Refusing database outside the existing dedicated Phase 3 database.')
    password = os.environ.get('NHS_DBT_PASSWORD')
    if not password:
        raise SystemExit('Missing transient NHS_DBT_PASSWORD environment variable')
    import psycopg
    from phase3_postgres import manifest_and_files
    root = args.root.resolve()
    model_inventory(root)
    manifest, digest = manifest_and_files(root)
    with psycopg.connect(host=args.host, port=args.port, user=args.user,
                         password=password, dbname=args.database, connect_timeout=8,
                         options='-c statement_timeout=120000') as conn:
        with conn.cursor() as cur:
            result = verify(cur,args.mode,root,manifest,digest)
            if args.mode == 'verify':
                cur.execute('''CREATE TABLE IF NOT EXISTS audit.phase7_dbt_builds (
                    project_sha256 char(64) NOT NULL,
                    source_manifest_sha256 char(64) NOT NULL,
                    schema_name text NOT NULL CHECK (schema_name='nhs_bi_marts'),
                    completed_at timestamptz NOT NULL DEFAULT now(),
                    classification text NOT NULL DEFAULT 'SIMULATION_ONLY',
                    PRIMARY KEY (project_sha256,source_manifest_sha256,schema_name)
                )''')
                cur.execute('''INSERT INTO audit.phase7_dbt_builds
                    (project_sha256,source_manifest_sha256,schema_name)
                    VALUES (%s,%s,%s) ON CONFLICT DO NOTHING''',
                    (result['phase7_model_sha256'],digest,BI_SCHEMA))
        if args.mode == 'verify':
            outfile = root/'data/processed/phase7_dbt_report.json'
            outfile.parent.mkdir(parents=True,exist_ok=True)
            outfile.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n',encoding='utf-8')
        print(json.dumps(result,indent=2,sort_keys=True),flush=True)

if __name__ == '__main__':
    main()
