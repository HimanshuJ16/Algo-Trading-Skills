---
name: insider-trading-controls-for-alternative-data-usage
description: >-
  Compliance governance engine for alternative data usage, auditing SEC Rule 10b-5 MNPI risks, PII anonymization thresholds, vendor due diligence sign-offs, and earnings blackout windows.
domain: Quant Research & Alt Data
subdomain: Compliance & Legal Governance for Alt Data
tags: ["alt-data", "insider-trading", "sec-rule-10b5", "mnpi", "pii-anonymization", "vendor-due-diligence", "compliance-governance"]
brokers_frameworks: ["SEC Rule 10b-5", "Section 204A Investment Advisers Act", "GDPR / CCPA PII Standards", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when onboarding alternative datasets (satellite imagery, credit card transactions, web scraping, consumer geolocation data) for quantitative trading strategies. Utilizing alternative data without rigorous compliance controls creates severe legal exposure under **SEC Rule 10b-5 (MNPI - Material Non-Public Information)**, Section 204A of the Investment Advisers Act, and data privacy laws (GDPR/CCPA). This module audits datasets for MNPI risks, vendor due diligence sign-offs, Terms of Service (ToS) compliance, PII anonymization panel sizes ($\ge 50$ observations), and earnings blackout windows.

## Prerequisites

- Alternative dataset specification (`dataset_name`, `data_source_type`, `has_mnpi_risk`, `has_vendor_diligence_signoff`, `is_tos_compliant`, `is_pii_scrubbed`, `panel_aggregation_count`, `hours_to_earnings_release`).
- Minimum panel aggregation threshold ($N \ge 50$ distinct consumer observations).
- Earnings blackout window ($\pm 48\text{ hours}$).

## Workflow

1. **SEC Rule 10b-5 MNPI Audit**:
   - Audit `has_mnpi_risk`. If dataset contains non-public material information derived from duty of confidentiality breaches or hacking $\implies$ Trigger `REJECTED_MNPI_RISK`.
2. **Vendor Due Diligence & Terms of Service (ToS) Audit**:
   - Audit `has_vendor_diligence_signoff == True` and `is_tos_compliant == True`.
3. **PII Anonymization & Panel Aggregation Audit**:
   - Audit `is_pii_scrubbed == True` and `panel_aggregation_count >= 50`.
4. **Earnings Release Blackout Audit**:
   - Verify `hours_to_earnings_release >= 48`. If within blackout window $\implies$ Trigger `BLACKOUT_WINDOW_RESTRICTED`.
5. **Audit Report Generation**: Output structured `AltDataComplianceReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ingesting Raw PII Consumer Feeds**: Using individual-level credit card or geolocation feeds without verifying PII scrubbing and $\ge 50$ panel aggregation, violating GDPR/CCPA and SEC guidelines.
- **Bypassing Vendor Due Diligence**: Trading on alternative datasets without obtaining written compliance representations from data vendors regarding legal rights to license data (e.g. SEC *App Annie* enforcement action).
- **Trading Alt-Data Signals During Earnings Blackout**: Routing aggressive alt-data trades within $\pm 48\text{h}$ of corporate earnings announcements when MNPI risk is highest.

## Verification

- Instantiate `AltDataInsiderTradingComplianceEngine`. Audit Compliant Satellite Dataset (No MNPI, Diligence Signoff Active, ToS OK, PII Scrubbed, Panel $= 250$, 72h to Earnings) $\implies$ verify `LOW_RISK_APPROVED`. Audit Contaminated MNPI Dataset (`has_mnpi_risk=True`) $\implies$ verify `REJECTED_MNPI_RISK`. Audit Raw Individual Feed (Panel $= 5 < 50$) $\implies$ verify `REJECTED_UNAGGREGATED_PII`.
- Run `python scripts/test_insider_trading_controls_for_alternative_data_usage.py`.

## Related Skills

- `alternative-data-vendor-due-diligence-checklist`
- `web-scraped-sentiment-data-pipeline`
---
