---
name: risk-reporting-for-external-stakeholders
description: >-
  Production-grade risk reporting engine generating customized, compliance-cleared risk reports for external stakeholders (LPs/Institutional Investors, Regulators, Prime Brokers, Auditors) with automated proprietary position redaction and cryptographic signature verification.
domain: Risk Management & Compliance Governance
subdomain: External Risk Disclosure & Stakeholder Reporting
tags: ["external-risk-reporting", "lp-reporting", "sec-form-pf", "aifmd-annex-iv", "information-barrier", "position-redaction"]
brokers_frameworks: ["SEC Form PF / FCA Annex IV", "AIFMD Regulatory Disclosures", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when preparing risk disclosure reports for external stakeholders, including Limited Partners (LPs), institutional investors, regulatory authorities (SEC Form PF, FCA Annex IV, AIFMD), prime brokers, and independent auditors. Quantitative trading firms must balance transparency requirements against protecting proprietary alpha signals and trade strategies. This engine enforces Information Barrier controls by automatically redacting individual position holdings while providing aggregate risk metrics (gross/net leverage, 99% VaR, Sharpe, sector concentrations, liquidity profiles) and cryptographic SHA-256 report signatures.

## Prerequisites

- Portfolio risk state (`fund_name`, `report_date_iso`, `total_aum_usd`, `net_asset_value_usd`, `gross_exposure_usd`, `net_exposure_usd`, `daily_var_99_pct`, `annualized_sharpe_ratio`, `max_drawdown_pct`, `top_sector_concentrations`, `liquidity_days_to_liquidate_pct`).
- Target stakeholder type (`LIMITED_PARTNER`, `REGULATOR`, `PRIME_BROKER`, `AUDITOR`).

## Workflow

1. **Leverage & Risk Metric Calculations**:
   - Compute Gross Leverage ($\frac{\text{Gross Exposure}}{\text{NAV}}$) and Net Leverage ($\frac{\text{Net Exposure}}{\text{NAV}}$).
2. **Proprietary Alpha Redaction**:
   - Strip individual constituent positions from report payload to prevent alpha reverse-engineering.
3. **Stakeholder Disclosure Tailoring**:
   - Tailor sector concentration and liquidity disclosure detail based on stakeholder clearance level.
4. **Cryptographic Manifest Signing**:
   - Compute SHA-256 report signature binding fund name, date, NAV, and gross leverage. Output structured `ExternalRiskReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Disclosing Raw Position Holdings**: Exposing specific stock/futures positions to external parties enables front-running or strategy reverse-engineering.
- **Inconsistent Regulatory Metrics**: Using non-standard VaR or leverage calculation methodologies on SEC Form PF filings.
- **Unverified External Deliverables**: Sending risk PDF reports without cryptographic hash verification or audit trail tracking.

## Verification

- Instantiate `RiskReportingForExternalStakeholdersEngine`. Generate LP risk report $\implies$ verify `gross_leverage` (2.5x), `net_leverage` (0.3x), top 5 sector concentrations disclosed, proprietary positions redacted (`are_proprietary_positions_redacted=True`), and SHA-256 report signature generated. Generate Regulator report $\implies$ verify full sector concentration disclosure.
- Run `python scripts/test_external_risk_reporter.py`.

## Related Skills

- `regulatory-capital-requirement-tracking`
- `risk-model-backtesting-against-realized-outcomes`
---
