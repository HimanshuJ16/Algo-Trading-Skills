---
name: custody-solution-vendor-due-diligence-checklist
description: Quantitative institutional due diligence framework for auditing digital
  asset custodians across SEC Qualified Custodian status, SOC 2 Type II compliance,
  bankruptcy remoteness, crime insurance, and MPC key security.
domain: Crypto Custody & Security
subdomain: Vendor Risk Management
tags:
- custody-due-diligence
- sec-qualified-custodian
- soc2-type2
- bankruptcy-remoteness
- crime-insurance
- vendor-risk
brokers_frameworks:
- SEC Rule 206(4)-2
- SOC 2 Type II
- Python Dataclasses
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when evaluating, onboarding, or conducting annual due diligence reviews of third-party digital asset custodians (e.g. Coinbase Custody, BitGo, Anchorage Digital, Fireblocks, Komainu). Under SEC Rule 206(4)-2 (Investment Advisers Custody Rule), registered investment advisers managing client crypto assets must engage an SEC Qualified Custodian. This module evaluates custodians across 5 core risk pillars (Regulatory, Security, Insurance, Operations, Governance), flags critical red flags, and computes a weighted Due Diligence Score ($0.0$ to $100.0$).

## Prerequisites

- Custodian vendor documentation (SOC 2 Type II report, insurance binder, SEC/State Trust charter, penetration test summary).
- Vendor assessment parameters (`vendor_name`, `charter_type`, `insurance_usd`, `has_soc2_type2`, `has_bankruptcy_remote_segregation`).

## Workflow

1. **Vendor Telemetry & Documentation Audit**:
   - Verify SEC Qualified Custodian status (State/Federal Trust charter or SEC Broker-Dealer).
   - Verify SOC 2 Type II clean opinion.
   - Verify Bankruptcy Remote Asset Segregation.
2. **Pillar Scoring & Red Flag Audit**:
   - **Regulatory Score (25%)**: Qualified Custodian status required.
   - **Security Score (25%)**: SOC 2 Type II + FIPS 140-2 L3 HSM/MPC.
   - **Insurance Score (20%)**: Specie/Crime insurance coverage vs asset scale.
   - **Operations Score (15%)**: $99.9\%+$ Uptime SLA + RTO $\le 4\text{h}$.
   - **Governance Score (15%)**: Segregation of duties + annual pen testing.
3. **Composite Scoring & Thresholding**:
   - Compute weighted Score ($0.0$ to $100.0$).
   - Flag **CRITICAL RED FLAGS** if Qualified Custodian status is missing or asset co-mingling is permitted.
4. **Audit Report Generation**: Output structured `CustodyVendorDueDiligenceReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Accepting SOC 2 Type I Instead of Type II**: Relying on SOC 2 Type I (point-in-time design description) rather than Type II (testing operational effectiveness over 6-12 months).
- **Ignoring Balance Sheet Co-mingling**: Failing to verify bankruptcy-remote asset segregation, exposing client assets to custodian creditors upon bankruptcy (e.g. Celsius/Voyager precedent).
- **Overestimating Insurance Coverage**: Assuming a \$100M insurance policy covers \$10B in AUM (covering only $1\%$ of assets under custody).

## Verification

- Instantiate `CustodyVendorDueDiligenceEngine`. Audit `Tier1_Trust_Custodian` (Qualified Custodian = True, Clean SOC 2 Type II = True, Bankruptcy Remote = True, Insurance = \$100M). Verify engine returns `APPROVED` status with Score $\ge 90.0$. Audit `Non_Compliant_Vendor` (Qualified Custodian = False, SOC 2 Type II = False). Verify engine flags `CRITICAL_RED_FLAG` and status `REJECTED`.
- Run `python scripts/test_custody_solution_vendor_due_diligence_checklist.py`.

## Related Skills

- `third-party-custody-audit-report-review-cadence`
- `insurance-coverage-assessment-for-custodied-crypto`
---
