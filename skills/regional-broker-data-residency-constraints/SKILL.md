---
name: regional-broker-data-residency-constraints
description: Use when deploying trading infrastructure across global jurisdictions
  to enforce regional data residency constraints (MiFID II, SEBI, GDPR, FINMA) and
  verify that trading bots run strictly within legally compliant cloud regions.
domain: algorithmic-trading
subdomain: broker-integration
tags:
- broker-integration
- data-residency
- compliance
- mifid-ii
- sebi
- cloud-regions
- regulatory-guard
brokers_frameworks:
- Data Residency Compliance Guard
- Python Cloud Security
version: '1.0'
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when deploying trading bots and market data feeds connecting to national or regional brokerages across global jurisdictions (e.g., SEBI-regulated brokers in India, MiFID II/GDPR-regulated brokers in Europe, FINMA in Switzerland). Regulations mandate that order execution logs, client identifiers, and financial data reside within approved domestic cloud regions (e.g. AWS `ap-south-1` Mumbai for India; AWS `eu-central-1` Frankfurt for Germany/EU). Running bots in non-compliant regions risks regulatory fines and latency penalties.

## Prerequisites

- Broker jurisdiction and allowed cloud region mapping table.
- Hosting environment metadata (`AWS_REGION`, `GCP_REGION`, `AZURE_REGION`, or IP geolocation).

## Workflow

1. **Register Broker Data Residency Rules**:
   - Map broker names to mandatory legal jurisdictions (`IN`, `EU`, `US`, `SG`, `UK`) and allowed cloud region codes.

2. **Probe Hosting Environment Region**:
   - Inspect environment variables (`AWS_REGION`, `AWS_DEFAULT_REGION`, `GCP_REGION`) or query metadata services (`169.254.169.254`).

3. **Audit Data Residency Compliance**:
   - Verify active hosting region satisfies target broker jurisdiction requirements.

4. **Enforce Compliance Veto**:
   - If hosting region breaches data residency constraints (e.g. running Zerodha bot in US-East-1), trip security alarm and block connection setup.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Multiregional Failover Violations**: Failing over to a backup cloud region that crosses international data residency boundaries.
- **Log Shipping Beyond Boundaries**: Centralizing raw order logs containing client PII into a US log aggregator from an EU/India bot without field redaction.
- **Relying on Default Cloud Fallbacks**: Deploying serverless functions without explicitly setting the target region.

## Verification

- Simulate connecting to Zerodha (India jurisdiction) from AWS `ap-south-1` (Mumbai) and verify compliance approval.
- Simulate connecting to Zerodha from AWS `us-east-1` (N. Virginia) and verify data residency compliance veto.
- Run `python scripts/test_residency_guard.py` and confirm 100% pass rate.

## Related Skills

- `mifid-ii-algo-trading-compliance-eu`
- `multi-region-failover-for-broker-connectivity`
- `structured-logging-for-post-incident-forensics`
---
