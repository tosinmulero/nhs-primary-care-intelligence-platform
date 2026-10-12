# NHS Primary Care Intelligence — engineering source snapshot

This directory contains **selected** Python, SQL, dbt and validation code from the independently developed, entirely synthetic primary-care demonstration.

The complete public-facing overview, six genuine Power BI screenshots and portable Power BI report are at the [repository root](../README.md). The sources in this directory are **not** a complete, one-click deployment of PostgreSQL, dbt and the clinical-workflow simulations.

## Python smoke-test environment

In VS Code with Python 3.12, from **this** directory:

```powershell
python -m pip install -e ".[dev]"
python -m pytest -q tests/test_contracts.py tests/test_phase2_dataset.py tests/test_phase8_powerbi.py
```

These selected tests exercise synthetic data contracts, reproducible fixture generation and the static Power BI build contract without needing a database. Other phase tests reference local generated data or unpublished engineering documentation and should not be treated as fully portable until those dependencies are packaged.

A root-level GitHub Actions workflow runs this limited, documented smoke-test scope. The historical `.github/workflows/ci.yml` included in this snapshot is not automatically discovered by GitHub Actions because it is nested below `engineering_source/`.

**Governance:** all source and datasets are fictional or simulated; no live NHS data or authorisation is involved.
