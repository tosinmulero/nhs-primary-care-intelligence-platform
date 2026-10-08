"""Phase 6: deploy and validate synthetic, aggregate-only PostgreSQL analytics.

This is a local demonstration, NOT patient care, a real NHS integration, or QOF reporting.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import sys
from pathlib import Path

SQL_RELATIVE_PATH = Path("sql/ddl/005_phase6_analytics.sql")
VIEW_METRICS = {
    "analytics.v_phase6_registration_monthly": ("registrations", "registrations", 8000),
    "analytics.v_phase6_population_demographics": ("patient_count", "patients", 8000),
    "analytics.v_phase6_appointments_monthly": ("appointment_count", "appointments", 20000),
    "analytics.v_phase6_coding_status": ("condition_records", "conditions", 3600),
    "analytics.v_phase6_patient_summary_status": ("summary_count", "patient_summaries", 1500),
    "analytics.v_phase6_terminology_mapping_status": ("mapping_count", "mappings", 8),
    "analytics.v_phase6_correspondence_queue": ("correspondence_count", "correspondence", 3200),
    "analytics.v_phase6_pathology_queue": ("result_count", "pathology", 4400),
    "analytics.v_phase6_task_performance": ("task_count", "admin_tasks", 6600),
    "analytics.v_phase6_qof_demo_trends": (None, "qof_measurements", 36),
    "analytics.v_phase6_quality_issue_trends": ("issue_count", "quality_issues", 300),
    "analytics.v_phase6_workflow_queue": ("case_count", "workflow_cases", 4749),
    "analytics.v_phase6_audit_daily": ("event_count", "audit_events", 7958),
}


def check_expected_views(sql_text: str) -> None:
    import re

    names = set(re.findall(r"CREATE\s+OR\s+REPLACE\s+VIEW\s+(analytics\.v_phase6_\w+)", sql_text, re.I))
    expected = set(VIEW_METRICS) | {
        "analytics.v_phase6_executive", "analytics.v_phase6_demo_cohorts"
    }
    if names != expected:
        raise ValueError(f"View inventory mismatch: missing={expected-names}, extra={names-expected}")
    prohibited = re.findall(r"\b(?:DROP|TRUNCATE|DELETE\s+FROM|ALTER\s+TABLE|UPDATE\s+core\.)\b", sql_text, re.I)
    if prohibited:
        raise ValueError("SQL migration contains a prohibited destructive operation")


def verify_reporting(cur, manifest: dict, digest: str, sql_digest: str) -> dict:
    from phase3_postgres import check_database

    check_database(cur, manifest, digest)
    cur.execute("SELECT to_regclass('audit.phase4_pipeline_runs'), to_regclass('workflow.phase5_cases')")
    if any(item is None for item in cur.fetchone()):
        raise ValueError("Phase 4 or Phase 5 structures are missing")
    cur.execute("SELECT count(*) FROM audit.phase4_pipeline_runs")
    if cur.fetchone()[0] < 1:
        raise ValueError("Phase 4 has not committed")
    cur.execute("SELECT count(*) FROM workflow.phase5_cases")
    if cur.fetchone()[0] != 4749:
        raise ValueError("Phase 5 queue must contain 4,749 simulation cases")
    counts: dict[str, int] = {}
    for view, (column, label, expected) in VIEW_METRICS.items():
        # All names are developer-owned literal allowlisted constants, never user-controlled.
        expression = f"coalesce(sum({column}),0)" if column else "count(*)"
        cur.execute(f"SELECT {expression} FROM {view}")
        actual = int(cur.fetchone()[0])
        if actual != expected:
            raise ValueError(f"{view}: {actual} rows or units, expected {expected}")
        counts[label] = actual
    cur.execute("SELECT registered_patients,intake_rows,accepted_rows,rejected_rows,total_demo_cases,awaiting_admin,awaiting_clinician,demo_approved,demo_rejected FROM analytics.v_phase6_executive")
    executive = cur.fetchone()
    if executive is None or tuple(executive[:5]) != (8000,8190,8000,190,4749):
        raise ValueError(f"Phase 6 KPI cross-check failed: {executive}")
    if sum(executive[5:]) != 4749:
        raise ValueError("Simulation workflow status counts do not reconcile")
    # The dashboard-facing views must never expose per-patient identifiers.
    cur.execute("""
        SELECT table_name,column_name FROM information_schema.columns
        WHERE table_schema='analytics' AND table_name LIKE 'v_phase6_%'
        AND column_name IN ('patient_id','nhs_number','date_of_birth','summary_text','demo_result_text')
    """)
    sensitive = cur.fetchall()
    if sensitive:
        raise ValueError(f"Unsafe patient-level field in dashboard view: {sensitive}")
    cur.execute("SELECT count(*) FROM audit.phase6_reporting_deployments WHERE sql_sha256=%s AND source_manifest_sha256=%s", (sql_digest,digest))
    if cur.fetchone()[0] != 1:
        raise ValueError("Phase 6 deployment marker missing or mismatched")
    cur.execute("SELECT reporting_date,intake_acceptance_pct FROM analytics.v_phase6_executive")
    as_of, acceptance = cur.fetchone()
    return {
        "classification": "SIMULATION_ONLY",
        "reporting_date": as_of.isoformat(),
        "data_scope": "AGGREGATE_ONLY_NO_PATIENT_IDENTIFIERS",
        "raw_intake_acceptance_pct": str(acceptance),
        "dashboard_reconciliations": counts,
        "registered_patients": 8000,
        "raw_intake_rows": 8190,
        "accepted_intake_rows": 8000,
        "rejected_intake_rows": 190,
        "total_demo_workflow_cases": 4749,
        "awaiting_demo_review": int(executive[5]) + int(executive[6]),
        "sql_sha256": sql_digest,
    }


def run(root: Path, host: str, port: int, user: str, database: str, password: str, mode: str, term: str) -> dict:
    import psycopg
    from phase3_postgres import DBNAME, manifest_and_files

    if not DBNAME.fullmatch(database):
        raise ValueError("Refusing a database outside nhs_primarycare_* naming")
    manifest, digest = manifest_and_files(root)
    sql_text = (root / SQL_RELATIVE_PATH).read_text(encoding="utf-8")
    check_expected_views(sql_text)
    sql_digest = hashlib.sha256(sql_text.encode("utf-8")).hexdigest()
    options = dict(host=host,port=port,user=user,dbname=database,password=password,
                   connect_timeout=8,options='-c statement_timeout=120000')
    with psycopg.connect(**options) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT to_regclass('audit.portfolio_metadata')")
            if cur.fetchone()[0] is None:
                raise ValueError("Project ownership metadata missing: refusing deployment")
            if mode == 'install':
                cur.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', ('NHS Primary Care Intelligence:PHASE6',))
                cur.execute(sql_text)
                cur.execute("""
                    INSERT INTO audit.phase6_reporting_deployments (sql_sha256,source_manifest_sha256)
                    VALUES (%s,%s) ON CONFLICT (sql_sha256) DO NOTHING
                """, (sql_digest,digest))
            report = verify_reporting(cur,manifest,digest,sql_digest)
            if mode == 'cohort':
                needle = term.strip()
                if not (2 <= len(needle) <= 60):
                    raise ValueError("Cohort term must be between 2 and 60 characters")
                cur.execute("""
                    SELECT code_system,concept_label,simulated_patient_count,suppressed_small_cell
                    FROM analytics.v_phase6_demo_cohorts
                    WHERE concept_label ILIKE %s ORDER BY concept_label
                """, ('%' + needle.replace('%','\\%').replace('_','\\_') + '%',))
                report['cohort_search'] = [dict(zip(
                    ('code_system','concept_label','simulated_patient_count','suppressed_small_cell'),
                    row)) for row in cur.fetchall()]
                report['cohort_search_term'] = needle
            # Only the 'install' mode commits the migration; report is deterministic.
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Synthetic NHS-style Phase 6 reporting analytics")
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--host',default='localhost')
    parser.add_argument('--port',type=int,default=5432)
    parser.add_argument('--user',default='postgres')
    parser.add_argument('--database',default='nhs_primarycare_demo')
    parser.add_argument('--mode',choices=('install','verify','cohort'),default='install')
    parser.add_argument('--term',default='blood pressure')
    opts=parser.parse_args()
    password=getpass.getpass('Local PostgreSQL password (not stored): ')
    try:
        report=run(opts.root.resolve(),opts.host,opts.port,opts.user,opts.database,password,opts.mode,opts.term)
        if opts.mode=='install':
            output=opts.root/'data'/'processed'/'phase6_reporting_report.json'
            output.parent.mkdir(parents=True,exist_ok=True)
            serial=json.dumps(report,indent=2,sort_keys=True)+'\n'
            if output.exists():
                if output.read_text(encoding='utf-8') != serial:
                    raise ValueError('Existing Phase 6 report is different; refusing overwrite')
            else:
                output.write_text(serial,encoding='utf-8')
        print('PHASE 6 PASS - aggregate reporting views reconciled to source data.')
        print(json.dumps(report,indent=2,sort_keys=True))
        print('SIMULATION ONLY. No real clinical records, NHS codes or validated QOF results.')
        return 0
    except Exception as exc:
        print(f'PHASE 6 FAILED: {type(exc).__name__}: {exc}',file=sys.stderr)
        return 1

if __name__=='__main__':
    raise SystemExit(main())
