---
name: regional-broker-data-residency-constraints
description: >-
  Regional data residency compliance guard verifying cloud hosting region alignment with broker legal jurisdictions (SEBI, GDPR, SEC), enforcing AWS/GCP region allowlists per broker.
domain: Broker Integration & Connectivity
subdomain: Data Residency & Regulatory Hosting Compliance
tags: ["data-residency", "cloud-region", "sebi", "gdpr", "sec", "broker-compliance", "aws", "gcp"]
brokers_frameworks: ["SEBI/RBI Data Localisation (India)", "EU GDPR/MiFID II", "US SEC/FINRA", "AWS Regions", "GCP Regions"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when deploying algorithmic trading systems on cloud infrastructure (AWS, GCP) that connect to brokers in jurisdictions with data residency regulations. India's SEBI/RBI requires financial data to be processed within India, the EU's GDPR/MiFID II requires data to remain in EU regions, and US SEC/FINRA has its own standards. This guard validates that the active cloud hosting region complies with the target broker's jurisdictional data residency policy, raising a violation error if the deployment region is non-compliant.

## Prerequisites

- Broker residency policies (built-in: Zerodha/Upstox=India, DEGIRO=EU, Alpaca=US).
- Cloud environment variables (`AWS_REGION`, `GCP_REGION`, or `TRADING_HOST_REGION`).

## Workflow

1. **Region Probe**:
   - Detect active cloud provider and region from environment variables.
2. **Policy Lookup**:
   - Map broker name to jurisdictional residency policy with allowed AWS/GCP regions.
3. **Compliance Validation**:
   - Check if current region is in the broker's allowed region set.
4. **Violation Handling**:
   - If non-compliant, raise `DataResidencyViolationError` with regulatory citation.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Deploying Indian Broker Bots in US Regions**: Running Zerodha/Upstox strategies on `us-east-1` violates SEBI data localisation.
- **Ignoring GCP Region Naming**: GCP uses different naming (`asia-south1`) vs AWS (`ap-south-1`); both must be checked.
- **No Policy for New Brokers**: Adding a new broker without registering its residency policy allows silent violations.

## Verification

- Instantiate `DataResidencyComplianceGuard`. Validate Zerodha in `ap-south-1` $\implies$ compliant (True). Validate Zerodha in `us-east-1` $\implies$ raises `DataResidencyViolationError` citing SEBI. Validate unregistered broker $\implies$ passes (no strict policy).
- Run `python scripts/test_residency_guard.py`.

## Related Skills

- `regulatory-custody-requirements-by-jurisdiction`
- `record-retention-periods-by-jurisdiction`
---
