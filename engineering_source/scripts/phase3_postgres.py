"""Phase 3: non-destructive PostgreSQL deployment of a fully synthetic NHS-style dataset.

Requires a local PostgreSQL account with CREATEDB (usually postgres). No NHS connections.
Credentials are provided only through transient environment variables.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
from pathlib import Path

try:
    import psycopg
    from psycopg import sql
except ImportError as exc:
    raise SystemExit('Install project dependencies in .venv first: pip install -e ".[dev]"') from exc

PROJECT = 'NHS Primary Care Intelligence | SIMULATION ONLY'
ORDER = [
    ('patients', 'core', 'patients'),
    ('staff', 'core', 'staff'),
    ('registrations', 'core', 'registrations'),
    ('appointments', 'core', 'appointments'),
    ('terminology_concepts', 'core', 'terminology_concepts'),
    ('terminology_mappings', 'core', 'terminology_mappings'),
    ('patient_conditions', 'core', 'patient_conditions'),
    ('hospital_correspondence', 'core', 'hospital_correspondence'),
    ('patient_summaries', 'core', 'patient_summaries'),
    ('pathology_results', 'core', 'pathology_results'),
    ('administrative_tasks', 'core', 'administrative_tasks'),
    ('data_quality_issues', 'core', 'data_quality_issues'),
    ('qof_demo_indicators', 'core', 'qof_demo_indicators'),
    ('qof_demo_measurements', 'core', 'qof_demo_measurements'),
    ('audit_events', 'audit', 'events'),
]
IDENT = re.compile(r'^[a-z][a-z0-9_]*$')
DBNAME = re.compile(r'^nhs_primarycare_[a-z0-9_]*$')


def manifest_and_files(root: Path) -> tuple[dict, str]:
    path = root / 'data' / 'processed' / 'phase2_manifest.json'
    raw_bytes = path.read_bytes()
    manifest = json.loads(raw_bytes)
    if (manifest.get('project') != PROJECT or manifest.get('patient_target') != 8000
            or manifest.get('seed') != 20261008):
        raise ValueError('Unexpected Phase 2 manifest: refusing to import')
    expected = {entry[0] for entry in ORDER}
    if set(manifest['tables']) != expected:
        raise ValueError('Expected precisely the 15 Phase 2 authoritative synthetic tables')
    for name, details in [*manifest['tables'].items(), ('patients_intake', manifest['raw_intake'])]:
        csv_path = root / 'data' / ('raw' if name == 'patients_intake' else 'synthetic') / f'{name}.csv'
        if details['file'] != csv_path.name or not csv_path.is_file():
            raise ValueError(f'Missing expected CSV: {csv_path}')
        digest = hashlib.sha256(csv_path.read_bytes()).hexdigest()
        if digest != details['sha256']:
            raise ValueError(f'CSV checksum mismatch: {name}; do not import modified data')
        if not (0 < details['rows'] < 1000000):
            raise ValueError(f'Unexpected row count in manifest: {name}')
    if manifest['tables']['patients']['rows'] != 8000 or manifest['raw_intake']['rows'] != 8190:
        raise ValueError('Unexpected patient volume')
    return manifest, hashlib.sha256(raw_bytes).hexdigest()


def copy_csv(cur, path: Path, schema: str, table: str) -> None:
    with path.open('r', encoding='utf-8', newline='') as stream:
        columns = next(csv.reader(stream))
    if not columns or len(set(columns)) != len(columns) or not all(IDENT.fullmatch(c) for c in columns):
        raise ValueError(f'Unexpected CSV columns in {path.name}')
    # Column names and schema are validated and quoted: never interpolate user input in SQL.
    stmt = sql.SQL("COPY {}.{} ({}) FROM STDIN WITH (FORMAT CSV, HEADER TRUE, NULL '')").format(
        sql.Identifier(schema), sql.Identifier(table),
        sql.SQL(', ').join(map(sql.Identifier, columns)),
    )
    with cur.copy(stmt) as writer, path.open('rb') as source:
        while block := source.read(65536):
            writer.write(block)


def check_database(cur, manifest: dict, manifest_hash: str) -> dict[str, int]:
    actual = {}
    for source, schema, table in ORDER:
        cur.execute(sql.SQL('SELECT count(*) FROM {}.{}').format(sql.Identifier(schema), sql.Identifier(table)))
        count = cur.fetchone()[0]
        actual[source] = count
        if count != manifest['tables'][source]['rows']:
            raise ValueError(f'Row-count mismatch: {schema}.{table}: {count} != expected {manifest["tables"][source]["rows"]}')
    cur.execute('SELECT count(*) FROM raw.patients_intake')
    if cur.fetchone()[0] != manifest['raw_intake']['rows']:
        raise ValueError('Raw patient intake count mismatch')
    cur.execute('SELECT count(*) FROM staging.v_clean_patient_intake')
    if cur.fetchone()[0] != 8000:
        raise ValueError('Deduplicated staging patient count must be 8000')
    cur.execute('SELECT count(*) FROM (SELECT patient_id FROM staging.v_clean_patient_intake EXCEPT SELECT patient_id FROM core.patients) AS missing')
    if cur.fetchone()[0] != 0:
        raise ValueError('Staging and authoritative patient identifiers disagree')
    cur.execute('SELECT active_patients FROM analytics.v_active_patient_count')
    if cur.fetchone()[0] != 8000:
        raise ValueError('Active patient view does not reconcile')
    cur.execute('SELECT malformed_ids, duplicate_ids, missing_area FROM analytics.v_intake_quality_summary')
    malformed, duplicates, missing = cur.fetchone()
    if (malformed, duplicates, missing) != (40, 150, 75):
        raise ValueError(f'Wrong deliberately injected quality issues: {(malformed, duplicates, missing)}')
    cur.execute('SELECT count(*) FROM core.patient_conditions pc JOIN core.staff s ON pc.reviewed_by=s.staff_id WHERE s.role_code NOT IN (%s,%s,%s)',
                ('GP', 'NURSE_PRACTITIONER', 'PRACTICE_NURSE'))
    if cur.fetchone()[0]:
        raise ValueError('Unapproved role recorded as clinical reviewer')
    cur.execute('SELECT manifest_sha256 FROM audit.portfolio_metadata WHERE project_key = %s', (PROJECT,))
    row = cur.fetchone()
    if row is None or row[0] != manifest_hash:
        raise ValueError('Portfolio metadata mismatch')
    return actual


def load(root: Path, host: str, port: int, user: str, database: str) -> None:
    if not DBNAME.fullmatch(database):
        raise ValueError('For safety the database name must start with nhs_primarycare_ and contain only lowercase letters, digits, underscores')
    manifest, digest = manifest_and_files(root)
    password = os.environ.get('NHSPC_DB_PASSWORD')
    if password is None:
        raise ValueError('Database password not provided via transient NHSPC_DB_PASSWORD environment variable')
    args = {'host': host, 'port': port, 'user': user, 'password': password, 'connect_timeout': 7}
    fresh = False
    admin = psycopg.connect(dbname='postgres', autocommit=True, **args)
    try:
        with admin.cursor() as cur:
            cur.execute('SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = %s)', (database,))
            exists = cur.fetchone()[0]
            if not exists:
                cur.execute(sql.SQL("CREATE DATABASE {} ENCODING 'UTF8'").format(sql.Identifier(database)))
                fresh = True
                print(f'CREATED new project database: {database}', flush=True)
            else:
                print(f'Existing database detected: {database}. Checking project ownership marker.', flush=True)
        try:
            with psycopg.connect(dbname=database, **args) as conn:
                with conn.cursor() as cur:
                    if not fresh:
                        cur.execute("SELECT to_regclass('audit.portfolio_metadata')")
                        if cur.fetchone()[0] is None:
                            raise ValueError('Existing database has no verified NHS portfolio marker. Refusing to modify it.')
                        cur.execute('SELECT manifest_sha256 FROM audit.portfolio_metadata WHERE project_key=%s', (PROJECT,))
                        row = cur.fetchone()
                        if row is None or row[0] != digest:
                            raise ValueError('Existing database has a different project identifier or dataset. Refusing to modify it.')
                        counts = check_database(cur, manifest, digest)
                        print('PHASE 3 PASS — already deployed and validated; no data changed.')
                        print('Rows: ' + ', '.join(f'{key}={value:,}' for key, value in counts.items()))
                        return
                    cur.execute((root / 'sql' / 'ddl' / '001_core_schema.sql').read_text(encoding='utf-8'))
                    cur.execute((root / 'sql' / 'ddl' / '002_phase3_auxiliary.sql').read_text(encoding='utf-8'))
                    for source, schema, table in ORDER:
                        path = root / 'data' / 'synthetic' / f'{source}.csv'
                        copy_csv(cur, path, schema, table)
                    copy_csv(cur, root / 'data' / 'raw' / 'patients_intake.csv', 'raw', 'patients_intake')
                    cur.execute('INSERT INTO audit.portfolio_metadata (project_key, manifest_sha256, dataset_seed, reporting_date) VALUES (%s,%s,%s,%s)',
                                (PROJECT, digest, manifest['seed'], manifest['as_of']))
                    counts = check_database(cur, manifest, digest)
                    # Every operation above, including all COPY commands, is committed in one transaction.
            # The psycopg connection context has now committed successfully.
            print('PHASE 3 PASS — new PostgreSQL database committed and validated.')
            print('Rows: ' + ', '.join(f'{key}={value:,}' for key, value in counts.items()))
            print('Active patients: 8,000 | Raw: 8,190 | Duplicates: 150 | Missing area: 75 | Malformed IDs: 40')
            print('Views: analytics.v_practice_overview, analytics.v_workflow_backlog, analytics.v_intake_quality_summary')
            print('SIMULATION ONLY: fictitious records, codes, mappings and QOF-style indicators.')
        except Exception:
            # A database newly created by this invocation has no pre-existing user data.
            if fresh:
                try:
                    with admin.cursor() as cur:
                        cur.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(database)))
                        print('Removed newly created empty project database after failed transaction. No pre-existing database was touched.', file=sys.stderr)
                except Exception as cleanup_exc:
                    print(f'Could not clean up newly-created database; manual inspection needed: {type(cleanup_exc).__name__}: {cleanup_exc}', file=sys.stderr)
            raise
    finally:
        admin.close()


if __name__ == '__main__':
    p = argparse.ArgumentParser(description='Deploy synthetic NHS-style primary care database to local PostgreSQL')
    p.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument('--host', default='localhost')
    p.add_argument('--port', type=int, default=5432)
    p.add_argument('--user', default='postgres')
    p.add_argument('--database', default='nhs_primarycare_demo')
    opts = p.parse_args()
    try:
        load(opts.root.resolve(), opts.host, opts.port, opts.user, opts.database)
    except (OSError, ValueError, psycopg.Error) as exc:
        print(f'PHASE 3 FAILED: {type(exc).__name__}: {exc}', file=sys.stderr)
        sys.exit(1)
