"""Phase 4: quality validation and idempotent read-only-source ETL.

Reads the existing fully synthetic PostgreSQL database; adds only Phase 4-managed
staging and audit objects. Never updates raw, core, or pre-existing audit events.
Passwords are prompted in the terminal and never written to disk.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import sys
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from phase3_postgres import PROJECT, check_database, manifest_and_files, DBNAME

VERSION = "phase4-quality-1.0.0"
SQL_FILE = Path("sql/ddl/003_phase4_quality.sql")
OUTPUT_FILE = Path("data/processed/phase4_quality_report.json")
EXPECTED_RULES = {
    "DUPLICATE_PATIENT_ID": 150,
    "MALFORMED_PATIENT_ID": 40,
    "MISSING_AREA_GROUP": 75,
}


def assess_quality(cur, manifest: dict) -> dict:
    cur.execute("SELECT * FROM analytics.v_phase4_quality_kpis")
    record = cur.fetchone()
    if record is None or cur.description is None:
        raise ValueError("Missing Phase 4 analytics KPI view")
    kpi = dict(zip([col.name for col in cur.description], record))
    cur.execute("SELECT rule_code, affected_rows FROM analytics.v_phase4_quality_issue_counts")
    rules = dict(cur.fetchall())
    if rules != EXPECTED_RULES:
        raise ValueError(f"Incorrect synthetic defect classification: {rules!r}")
    expected_rows = int(manifest["raw_intake"]["rows"])
    expected_patients = int(manifest["tables"]["patients"]["rows"])
    if (kpi["intake_rows"], kpi["accepted_rows"], kpi["rejected_rows"],
        kpi["curated_patients"], kpi["active_registrations"]) != (
        expected_rows, expected_patients, expected_rows - expected_patients,
        expected_patients, expected_patients
    ):
        raise ValueError(f"Source-to-target row counts do not reconcile: {kpi!r}")
    # Bidirectional EXCEPT compares the actual values, not just counts.
    cur.execute("""
      SELECT
        (SELECT count(*) FROM (
           SELECT patient_id, date_of_birth, sex_at_registration, area_group
           FROM staging.phase4_patient_intake_curated
           EXCEPT
           SELECT patient_id, date_of_birth, sex_at_registration, area_group
           FROM staging.v_clean_patient_intake
        ) x) +
        (SELECT count(*) FROM (
           SELECT patient_id, date_of_birth, sex_at_registration, area_group
           FROM staging.v_clean_patient_intake
           EXCEPT
           SELECT patient_id, date_of_birth, sex_at_registration, area_group
           FROM staging.phase4_patient_intake_curated
        ) y) AS mismatch_count
    """)
    if cur.fetchone()[0] != 0:
        raise ValueError("Curated staging does not match the validated clean intake")
    cur.execute("SELECT count(*) FROM staging.phase4_patient_intake_curated WHERE source_manifest_sha256 <> %s", (manifest["_sha256"],))
    if cur.fetchone()[0]:
        raise ValueError("Curated staging has unrecognised dataset provenance")
    cur.execute("SELECT count(*) FROM core.patient_conditions WHERE review_status IN ('approved','rejected') AND (reviewed_by IS NULL OR reviewed_at IS NULL)")
    if cur.fetchone()[0]:
        raise ValueError("Simulated clinical reviewer metadata is incomplete")
    cur.execute("SELECT count(*) FROM core.pathology_results WHERE review_status IN ('reviewed','filed') AND (reviewed_by IS NULL OR reviewed_at IS NULL)")
    if cur.fetchone()[0]:
        raise ValueError("Simulated pathology reviewer metadata is incomplete")
    return {
        "classification": "SIMULATION_ONLY",
        "pipeline_version": VERSION,
        "dataset_as_of": str(kpi["synthetic_as_of"]),
        "raw_intake_rows": kpi["intake_rows"],
        "accepted_intake_rows": kpi["accepted_rows"],
        "rejected_intake_rows": kpi["rejected_rows"],
        "intake_acceptance_pct": float(kpi["intake_acceptance_pct"]),
        "curated_patient_rows": kpi["curated_patients"],
        "active_registrations": kpi["active_registrations"],
        "open_curated_quality_issues": kpi["outstanding_quality_issues"],
        "outstanding_admin_tasks": kpi["outstanding_tasks"],
        "raw_intake_rule_hits": dict(sorted(rules.items())),
        "explanation": "Rule-hit counts can overlap. Intake acceptance is not a clinical quality score.",
    }


def run(root: Path, host: str, port: int, user: str, db: str, password: str) -> dict:
    if not DBNAME.fullmatch(db):
        raise ValueError("Refusing to connect outside nhs_primarycare_* project databases")
    manifest, digest = manifest_and_files(root)
    manifest["_sha256"] = digest
    run_key = hashlib.sha256(f"{digest}:{VERSION}".encode()).hexdigest()
    with psycopg.connect(dbname=db, host=host, port=port, user=user,
                         password=password, connect_timeout=8,
                         options="-c statement_timeout=120000") as conn:
        with conn.cursor() as cur:
            # Database identity validated before running ANY DDL or inserts.
            cur.execute("SELECT to_regclass('audit.portfolio_metadata')")
            if cur.fetchone()[0] is None:
                raise ValueError("Database lacks Phase 3 portfolio ownership marker")
            check_database(cur, manifest, digest)
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"{PROJECT}:{VERSION}",))
            # Additive, scoped migration. Any failure rolls back these changes.
            cur.execute((root / SQL_FILE).read_text(encoding="utf-8"))
            cur.execute("""
              INSERT INTO staging.phase4_patient_intake_curated
                  (patient_id, date_of_birth, sex_at_registration, area_group,
                   source_row, source_manifest_sha256)
              SELECT patient_id, date_of_birth::date, sex_at_registration,
                     area_group, intake_row_number, %s
              FROM staging.v_phase4_patient_intake_decisions
              WHERE intake_decision = 'ACCEPT'
              ON CONFLICT(patient_id) DO NOTHING
            """, (digest,))
            report = assess_quality(cur, manifest)
            cur.execute("""
              INSERT INTO audit.phase4_pipeline_runs
                  (run_key, source_manifest_sha256, pipeline_version, report_json)
              VALUES (%s, %s, %s, %s)
              ON CONFLICT(run_key) DO NOTHING
            """, (run_key, digest, VERSION, Jsonb(report)))
            cur.execute("SELECT report_json FROM audit.phase4_pipeline_runs WHERE run_key=%s", (run_key,))
            stored = cur.fetchone()[0]
            if stored != report:
                raise ValueError("Existing run history differs from current validated quality report")
    # Database has committed; write report without changing any pre-existing file.
    target = root / OUTPUT_FILE
    content = json.dumps({"project": PROJECT, "manifest_sha256": digest,
                          "run_key": run_key, **report}, indent=2, sort_keys=True) + "\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_text(encoding="utf-8") != content:
            raise FileExistsError(f"Existing report differs. Refusing overwrite: {target}")
    else:
        # Exclusive creation: fail safely if another process created the file.
        with target.open("x", encoding="utf-8", newline="\n") as f:
            f.write(content)
    return report


def main() -> int:
    p = argparse.ArgumentParser(description="Synthetic NHS portfolio Phase 4 quality ETL")
    p.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument("--host", default="localhost")
    p.add_argument("--port", type=int, default=5432)
    p.add_argument("--user", default="postgres")
    p.add_argument("--database", default="nhs_primarycare_demo")
    a = p.parse_args()
    # A password is used only in process memory (unlike storing it in .env).
    pwd = getpass.getpass("Local PostgreSQL password (not stored): ")
    try:
        report = run(a.root.resolve(), a.host, a.port, a.user, a.database, pwd)
    except (OSError, ValueError, psycopg.Error) as exc:
        print(f"PHASE 4 FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print("PHASE 4 PASS - data-quality ETL validated and committed.")
    for k in ("raw_intake_rows", "accepted_intake_rows", "rejected_intake_rows",
              "intake_acceptance_pct", "curated_patient_rows", "active_registrations",
              "open_curated_quality_issues", "outstanding_admin_tasks"):
        print(f"{k}: {report[k]}")
    print("Audit: audit.phase4_pipeline_runs | SQL: analytics.v_phase4_quality_kpis")
    print("SIMULATION ONLY - never connect to actual NHS records or use for care decisions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
