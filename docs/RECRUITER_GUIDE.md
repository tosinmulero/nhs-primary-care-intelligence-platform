# Recruiter evidence guide — NHS Primary Care Data Management & Clinical Intelligence Platform

[**Full case study on Mulero Analytics**](https://tosinmulero.github.io/data-analytics-portfolio/case-studies/nhs-primary-care-intelligence-platform.html) · [Repository README](../README.md) · [Main portfolio](https://tosinmulero.github.io/data-analytics-portfolio/projects.html)

> **Data provenance:** Entirely synthetic GP administration and clinical-intelligence scenario. No patient information, actual clinical systems or NHS endorsement.

## The question

How can a hypothetical GP practice validate administrative records, prioritise review queues and produce trustworthy operational reporting?

## Technical approach

- Deterministic synthetic record generation and Python validation.
- PostgreSQL/SQL raw-to-reporting transformations and dbt aggregate marts.
- Power BI PBIP/PBIR report plus a portable, aggregate-CSV demo.

## Documented results to review

- 8,000 fictional registered patients and 20,000 appointments in the scenario.
- 4,749 simulated review cases; 3,240 outstanding administrative tasks.
- Six Power BI pages and 12 aggregate marts, backed by validation and documented assumptions.

## Evidence map

| Review item | Source | What it supports |
|---|---|---|
| Executive dashboard screenshot | [Open](../docs/images/page-01-executive.png) | See the reporting surface |
| Other five dashboard images | [Open](../docs/images/page-06-governance.png) | Governance and operational monitoring view |
| Engineering source guide | [Open](../engineering_source/README.md) | Scope of published source code |
| Portable report configuration | [Open](../configure_portable_demo.py) | How the report is prepared locally |

## Reproducibility and limitations

The README documents running `python configure_portable_demo.py` and opening the generated local PBIP project in Power BI Desktop. The engineering source is not presented as a turnkey live-PostgreSQL deployment.

The repository README and linked source files remain the primary technical record. Numbers in this guide summarise the documented project artefacts; they are not claims of a third-party deployment, independently verified business impact or a live customer engagement.

## Reviewer path

1. Scan the [portfolio case study](https://tosinmulero.github.io/data-analytics-portfolio/case-studies/nhs-primary-care-intelligence-platform.html) for the problem and conclusions.
2. Open the main visual and supporting artefacts from the evidence map.
3. Review the repository README for tooling, methods, reproducibility instructions and limitations.
4. Inspect the code and workflows if deeper technical assurance is required.

---

*Independent portfolio work by Oluwatosin Mulero. Technical details and evidence are maintained in this repository.*
