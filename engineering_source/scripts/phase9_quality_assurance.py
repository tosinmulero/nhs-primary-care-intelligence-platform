"""Read-only quality assurance of the NHS Primary Care synthetic portfolio.

Run from VS Code using the project's Python environment. This script reads the
existing source files and aggregate CSV snapshots, reruns pytest, and writes a
new timestamped QA report. It does not edit Power BI source, CSVs, or PostgreSQL.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys

BASE = "NHS_Primary_Care_Analytics"
MARTS = (
    "mart_p7_executive", "mart_p7_demographics", "mart_p7_registrations",
    "mart_p7_appointments", "mart_p7_record_quality", "mart_p7_inbox_aging",
    "mart_p7_admin_tasks", "mart_p7_workflow_queues", "mart_p7_qof_demo",
    "mart_p7_data_quality", "mart_p7_audit_activity", "mart_p7_demo_cohorts",
)
DISALLOWED_COLUMNS = {
    "patient_id", "nhs_number", "patient_name", "date_of_birth", "dob",
    "full_name", "email_address", "email", "phone_number", "telephone",
    "postcode", "street_address", "home_address", "national_insurance_number",
}
EXPECTED_TOTALS = {
    "registered_patients": 8000,
    "appointments_total": 20000,
    "tasks_outstanding": 3240,
    "accepted_rows": 8000,
    "rejected_rows": 190,
    "intake_rows": 8190,
    "total_demo_cases": 4749,
}
MIN_TESTS = 41


class Checks:
    def __init__(self):
        self.results: list[dict] = []
        self.notes: list[str] = []

    def check(self, label: str, action):
        try:
            detail = action()
            self.results.append({"check": label, "status": "PASS", "detail": str(detail)})
            print(f"PASS | {label}: {detail}")
        except Exception as exc:
            self.results.append({"check": label, "status": "FAIL", "detail": str(exc)})
            print(f"FAIL | {label}: {exc}")

    def warning(self, message: str):
        self.notes.append(message)
        print("REVIEW | " + message)


def require(ok, explanation):
    if not ok:
        raise AssertionError(explanation)


def read_json(path: Path):
    require(path.is_file(), f"Missing file: {path}")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def check_project_files(root: Path):
    required = [
        root / "README.md",
        root / "powerbi" / f"{BASE}.pbip",
        root / "data" / "processed" / "phase2_manifest.json",
        root / "data" / "processed" / "phase4_quality_report.json",
        root / "data" / "processed" / "phase7_dbt_report.json",
        root / "data" / "processed" / "phase8_powerbi_manifest.json",
    ]
    missing = [str(p.relative_to(root)) for p in required if not p.is_file()]
    require(not missing, "Missing prerequisite files: " + ", ".join(missing))
    read_json(required[3]); read_json(required[4]); read_json(required[5])
    return f"{len(required)} prerequisite files present; Phase 4/7/8 reports readable"


def check_model(root: Path):
    directory = root / "powerbi" / f"{BASE}.SemanticModel"
    require(directory.is_dir(), "Semantic model folder not found")
    bim = directory / "model.bim"
    if bim.exists():
        tables = read_json(bim)["model"]["tables"]
        names = {t["name"] for t in tables}
        for t in tables:
            cols = {c["name"].casefold() for c in t.get("columns", [])}
            measures = {m["name"].casefold() for m in t.get("measures", [])}
            require(not (cols & measures), f"Measure/column conflict: {t['name']}")
        kind = "BIM"
        n = len(tables)
    else:
        table_directory = directory / "definition" / "tables"
        table_files = list(table_directory.glob("*.tmdl")) if table_directory.is_dir() else []
        require(table_files, "Neither model.bim nor TMDL table definitions found")
        names = {f.stem for f in table_files}
        require(all(f.stat().st_size for f in table_files), "An empty TMDL table definition was detected")
        kind = "TMDL"
        n = len(table_files)
    missing = sorted(set(MARTS) - names)
    require(not missing, "Reporting marts missing from semantic model: " + str(missing))
    return f"{kind}; {n} total tables; all 12 marts present"


def check_report_pages(root: Path):
    page_dir = root / "powerbi" / f"{BASE}.Report" / "definition" / "pages"
    meta = read_json(page_dir / "pages.json")
    page_ids = meta.get("pageOrder", [])
    require(len(page_ids) == 6 and len(set(page_ids)) == 6, f"Expected six unique pages, got {len(page_ids)}")
    counts: Counter = Counter()
    for num, page_id in enumerate(page_ids, start=1):
        page = read_json(page_dir / page_id / "page.json")
        require(bool(page.get("displayName", "").strip()), f"Page {num} has no display name")
        vfiles = list((page_dir / page_id / "visuals").rglob("visual.json"))
        require(len(vfiles) >= 11, f"Page {num} has only {len(vfiles)} visuals")
        local: Counter = Counter()
        for f in vfiles:
            obj = read_json(f)
            kind = obj.get("visual", {}).get("visualType")
            require(isinstance(kind, str) and bool(kind), f"Visual has no type: {f}")
            local[kind] += 1
        require(local.get("cardVisual", 0) >= 4, f"Page {num}: expected 4 KPI cards")
        chart_count = sum(local.get(x, 0) for x in ("lineChart", "columnChart", "barChart", "donutChart"))
        require(chart_count >= 4, f"Page {num}: expected 4 data charts")
        require(local.get("pageNavigator", 0) >= 1, f"Page {num}: page navigator missing")
        require(local.get("slicer", 0) >= 1, f"Page {num}: slicer missing")
        counts.update(local)
    total = sum(counts.values())
    require(total >= 78, f"Expected at least 78 visuals, found {total}")
    require(counts["cardVisual"] >= 24, "Fewer than 24 KPI cards found")
    return f"6 pages, {total} PBIR visuals, {counts['cardVisual']} KPI cards, {counts['slicer']} slicers"


def check_snapshot_marts(root: Path):
    manifest = read_json(root / "data" / "processed" / "phase8_powerbi_manifest.json")
    descriptor = manifest.get("marts", {})
    require(set(MARTS) <= set(descriptor), "Snapshot manifest is missing required marts")
    snapshots = root / "data" / "processed" / "powerbi_marts"
    rows: dict[str, list[dict]] = {}
    counts = {}
    for mart in MARTS:
        path = snapshots / f"{mart}.csv"
        require(path.is_file(), f"Missing CSV: {path.name}")
        with path.open("r", newline="", encoding="utf-8-sig") as file:
            reader = csv.DictReader(file)
            headers = reader.fieldnames or []
            require(headers, f"Missing header: {mart}")
            lowered = {x.casefold() for x in headers}
            require(not lowered.intersection(DISALLOWED_COLUMNS), f"Patient identifier column found in {mart}")
            require(not any("patient_id" in x or "nhs_number" in x for x in lowered), f"Patient ID column found in {mart}")
            require(headers == descriptor[mart]["columns"], f"Column drift: {mart}")
            data = list(reader)
        require(len(data) > 0, f"Mart is empty: {mart}")
        require(len(data) == int(descriptor[mart]["rows"]), f"Manifest row-count mismatch: {mart}")
        rows[mart] = data
        counts[mart] = len(data)
    return rows, counts


def check_snapshot_integrity(root: Path):
    _, counts = check_snapshot_marts(root)
    return f"12 CSV marts verified against schema/row-count manifest; {sum(counts.values())} aggregate rows"


def check_reconciliation(root: Path):
    rows, _ = check_snapshot_marts(root)
    exec_rows = rows["mart_p7_executive"]
    require(len(exec_rows) == 1, "Expected exactly one executive snapshot row")
    row = exec_rows[0]
    for field, expected in EXPECTED_TOTALS.items():
        require(int(float(row[field])) == expected, f"Executive {field} expected {expected}, got {row[field]}")
    data_checks = {
        "mart_p7_appointments": ("appointment_count", 20000),
        "mart_p7_registrations": ("registrations", 8000),
        "mart_p7_demographics": ("patient_count", 8000),
        "mart_p7_workflow_queues": ("case_count", 4749),
        "mart_p7_admin_tasks": ("task_count", 6600),
        "mart_p7_data_quality": ("issue_count", 300),
    }
    for mart, (column, expected) in data_checks.items():
        actual = sum(int(float(r[column])) for r in rows[mart])
        require(actual == expected, f"{mart} {column} expected {expected}, got {actual}")
    return "Executive 8,000 patients / 20,000 appointments / 3,240 outstanding tasks; six supporting marts reconciled"


def check_theme(root: Path):
    theme_file = (root / "powerbi" / f"{BASE}.Report" / "StaticResources"
                  / "RegisteredResources" / "NHS_Primary_Care_Theme.json")
    theme = read_json(theme_file)
    serialized = json.dumps(theme).casefold()
    matches = [c for c in ("#112744", "#168bdf", "#11af9e", "#8254d8", "#f2ad3a", "#f16b7a") if c in serialized]
    require(len(matches) >= 3, f"Expected NHS-inspired colour palette; found {len(matches)} colours")
    return f"Registered report theme available; {len(matches)} expected palette colours present"


def check_python_tests(root: Path):
    try:
        process = subprocess.run(
            [sys.executable, "-m", "pytest", "-q"], cwd=root, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=180, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("pytest exceeded 180 seconds") from exc
    output = (process.stdout or "") + "\n" + (process.stderr or "")
    print("PYTEST: " + output.strip()[-1300:])
    require(process.returncode == 0, f"pytest exit code {process.returncode}")
    match = re.search(r"(\d+) passed", output)
    require(match is not None, "pytest completed without a recognisable passed-test summary")
    n = int(match.group(1))
    require(n >= MIN_TESTS, f"Expected at least {MIN_TESTS} tests, got {n}")
    return f"{n} automated tests passed"


def check_github_readiness(root: Path):
    gitignore = root / ".gitignore"
    require(gitignore.is_file(), ".gitignore missing")
    content = gitignore.read_text(encoding="utf-8-sig").lower()
    require(".venv" in content, "Virtual environment missing from .gitignore")
    require(".env" in content, "Environment-secret file missing from .gitignore")
    readme = root / "README.md"
    text = readme.read_text(encoding="utf-8-sig").lower()
    require("synthetic" in text or "simulation" in text or "fictional" in text, "README must clearly label the synthetic data")
    git = "initialised" if (root / ".git").exists() else "not initialised (can be configured in Phase 10)"
    return f".gitignore protects environment files; README labels simulation; Git {git}"


def render_markdown(payload: dict):
    lines = [
        "# Phase 9 — NHS Primary Care Portfolio Quality Assurance",
        "",
        f"Generated (UTC): {payload['timestamp_utc']}",
        "",
        f"**Automated outcome:** {payload['status']}",
        "",
        "All project records are fictitious. This report does not validate any medical or clinical decisions.",
        "",
        "## Automated checks",
        "",
        "| Check | Status | Details |",
        "|---|---|---|",
    ]
    for result in payload["checks"]:
        detail = result["detail"].replace("|", "/").replace("\n", " ")
        lines.append(f"| {result['check']} | {result['status']} | {detail} |")
    lines += [
        "", "## Manual acceptance", "",
        "- [x] User reports that all six pages render acceptably in Power BI Desktop.",
        "- [ ] Verify that each page's slicer filters the intended visual(s), then clear selection.",
        "- [ ] Verify navigation across all six pages and back to the executive page.",
        "- [ ] Confirm no visual errors after closing and reopening the saved PBIP.",
        "- [ ] Capture actual Power BI Desktop screenshots for portfolio publication.",
        "", "## Design limitations and publication cautions", "",
    ]
    for note in payload["review_notes"]:
        lines.append("- " + note)
    lines += [
        "", "This audit checks local files and snapshots. It does not execute SQL against PostgreSQL, "
        "prove interactivity, guarantee the PBIP works on another computer, or publish anything online.", "",
    ]
    return "\n".join(lines)


def run(root: Path):
    root = root.expanduser().resolve()
    require(root.is_dir(), f"Project folder not found: {root}")
    tracker = Checks()
    tracker.check("Source files and manifests", lambda: check_project_files(root))
    tracker.check("Power BI semantic model", lambda: check_model(root))
    tracker.check("PBIR pages and visual structure", lambda: check_report_pages(root))
    tracker.check("Aggregate CSV manifests and identifiers", lambda: check_snapshot_integrity(root))
    tracker.check("Cross-mart KPI reconciliation", lambda: check_reconciliation(root))
    tracker.check("Colour theme integrity", lambda: check_theme(root))
    tracker.check("Python automated test suite", lambda: check_python_tests(root))
    tracker.check("Publication security basics", lambda: check_github_readiness(root))

    tracker.warning("Power BI uses workstation-specific CSV snapshot paths, while data/processed is Git-ignored. Phase 10 must provide a reproducible export and path-reconfiguration workflow before others can refresh the published PBIP.")
    tracker.warning("Aggregated marts have limited cross-table slicer behaviour; synchronized slicers and drill-through are not proven by visual review.")
    tracker.warning("Report contains synthetic demonstration metrics, including unofficial QOF-style indicators; do not represent it as an NHS production system.")
    tracker.warning("Manual checks for each slicer, page navigation, reopening the saved file and authentic screenshots remain before publication.")

    status = "PASS_AUTOMATED" if all(x["status"] == "PASS" for x in tracker.results) else "FAIL_AUTOMATED"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload = {
        "phase": 9,
        "status": status,
        "timestamp_utc": timestamp,
        "project_root": str(root),
        "classification": "SYNTHETIC_SIMULATION_ONLY",
        "checks": tracker.results,
        "review_notes": tracker.notes,
        "powerbi_visual_review": "USER_CONFIRMED_ALL_SIX_PAGES",
        "interaction_test_status": "MANUAL_CONFIRMATION_PENDING",
        "publication_status": "NOT_PUBLISHED",
    }
    output_dir = root / "data" / "processed"
    docs_dir = root / "docs"
    output_dir.mkdir(parents=True, exist_ok=True)
    docs_dir.mkdir(parents=True, exist_ok=True)
    json_file = output_dir / f"phase9_qa_report_{timestamp}.json"
    md_file = docs_dir / f"PHASE9_QA_RESULTS_{timestamp}.md"
    require(not json_file.exists() and not md_file.exists(), "QA report for this timestamp already exists; rerun in one minute")
    with json_file.open("x", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")
    with md_file.open("x", encoding="utf-8") as f:
        f.write(render_markdown(payload))
    print("\nRESULT: " + status)
    print("JSON REPORT: " + str(json_file))
    print("MARKDOWN REPORT: " + str(md_file))
    print("No PostgreSQL tables, data extracts or Power BI source files were modified.")
    return 0 if status == "PASS_AUTOMATED" else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Phase 9 local QA for the synthetic NHS portfolio")
    ap.add_argument("--project-root", required=True, type=Path)
    a = ap.parse_args()
    try:
        sys.exit(run(a.project_root))
    except Exception as exc:
        print("PHASE 9 QA ERROR: " + str(exc), file=sys.stderr)
        sys.exit(2)
