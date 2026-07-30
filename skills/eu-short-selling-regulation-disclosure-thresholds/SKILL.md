---
name: eu-short-selling-regulation-disclosure-thresholds
description: Quantitative European regulatory compliance engine for tracking EU Short
  Selling Regulation (SSR - Regulation 236/2012) net short position disclosures (0.1%
  NCA private notification / 0.5% public disclosure) and naked short bans.
domain: Regulatory Compliance & Governance
subdomain: European Short Selling Regulation (EU SSR)
tags:
- eu-ssr
- short-selling-regulation
- nca-notification
- public-disclosure
- naked-short-ban
- locate-audit
- mifid-ii
brokers_frameworks:
- Regulation (EU) No 236/2012
- ESMA SSR Register
- Python Dataclasses
version: 1.0.0
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in European quantitative equity trading, prime brokerage position reporting, and risk management systems. Under **EU Short Selling Regulation (SSR - Regulation (EU) No 236/2012)**, firms holding net short positions in EU-admitted shares must privately notify the National Competent Authority (NCA) at **0.1%** of issued share capital and publicly disclose positions to the market at **0.5%** (with 0.1% incremental step notifications). Furthermore, uncovered (naked) short selling is strictly prohibited, requiring locate/borrow verification prior to short order entry.

## Prerequisites

- Stock ISIN / Symbol (`isin`, `symbol`, `issued_share_capital`).
- Portfolio long and short position share counts (`long_shares_qty`, `short_shares_qty`).
- Locate / borrow agreement status (`has_valid_locate_agreement`: True/False).

## Workflow

1. **Net Short Position Percentage Calculation**:
   - $\text{Net Short Shares} = \text{Short Shares} - \text{Long Shares}$.
   - $\text{Net Short \%} = \frac{\text{Net Short Shares}}{\text{Issued Share Capital}} \times 100\%$.
2. **Naked Short Selling Ban Audit**:
   - If $\text{Net Short Shares} > 0$ and `has_valid_locate_agreement` is False $\implies$ Flag `NAKED_SHORT_BAN_BREACH`.
3. **Statutory Disclosure Threshold Check**:
   - If $\text{Net Short \%} \ge 0.5\% \implies$ Flag `PUBLIC_DISCLOSURE_REQUIRED` (Filing deadline: $T+1$ 15:30 CET).
   - Else if $\text{Net Short \%} \ge 0.1\% \implies$ Flag `PRIVATE_NCA_NOTIFICATION_REQUIRED` (Filing deadline: $T+1$ 15:30 CET).
   - Else $\implies$ `BELOW_REPORTING_THRESHOLDS`.
4. **Audit Report Generation**: Output structured `EuSsrDisclosureReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Failing the 0.1% Private NCA Notification Threshold**: Relying on outdated 0.2% thresholds, failing to notify NCAs when positions cross 0.1% of share capital.
- **Missing T+1 15:30 CET Reporting Cutoff**: Delaying SSR filings beyond the 15:30 CET cutoff on the trading day following position crossing.
- **Executing Naked Short Orders**: Executing short equity orders without documented locates or binding borrow agreements, breaching Article 12 naked short prohibitions.

## Verification

- Instantiate `EuShortSellingRegulationEngine`. Input company with 100,000,000 issued shares. Hold net short position of 600,000 shares ($0.60\%$). Verify engine flags `PUBLIC_DISCLOSURE_REQUIRED` ($0.60\% \ge 0.50\%$) and specifies $T+1$ 15:30 CET deadline. Submit net short position of 200,000 shares ($0.20\%$). Verify engine flags `PRIVATE_NCA_NOTIFICATION_REQUIRED`. Submit short order without locate. Verify engine blocks `NAKED_SHORT_BAN_BREACH`.
- Run `python scripts/test_eu_short_selling_regulation_disclosure_thresholds.py`.

## Related Skills

- `eu-market-abuse-regulation-mar-surveillance`
- `exchange-self-match-prevention-configuration`
---
