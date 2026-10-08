"""Minimal filesystem validation of the Phase 1 repository contract."""
from pathlib import Path

root = Path(__file__).resolve().parents[1]
needed = [
    "README.md", "pyproject.toml", "sql/ddl/001_core_schema.sql",
    "docs/ARCHITECTURE.md", "docs/DATA_DICTIONARY.md",
    "docs/GOVERNANCE.md", "docs/DASHBOARD_SPEC.md", "docs/ROADMAP.md",
    "src/primarycare/contracts.py", "tests/test_contracts.py",
    "dbt/dbt_project.yml", ".github/workflows/ci.yml", ".env.example",
]
missing = [name for name in needed if not (root / name).is_file()]
if missing:
    raise SystemExit("PHASE 1 FAIL — missing files: " + ", ".join(missing))
content = (root / "sql/ddl/001_core_schema.sql").read_text(encoding="utf-8")
for schema in ("raw", "staging", "core", "analytics", "audit"):
    if f"CREATE SCHEMA IF NOT EXISTS {schema};" not in content:
        raise SystemExit(f"PHASE 1 FAIL — missing schema declaration: {schema}")
print("PHASE 1 PASS — expected files and schema declarations present")
print("Phase 1 does not create a database, generate patients, or build a dashboard.")