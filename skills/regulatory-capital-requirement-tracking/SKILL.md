---
name: regulatory-capital-requirement-tracking
description: >-
  Regulatory capital tracking engine evaluating liquid assets, illiquid deductions, liabilities, and subordinated debt against minimum regulatory capital frameworks (SEC Rule 15c3-1, Basel III, FCA IFPR).
domain: Regulatory & Financial Compliance
subdomain: Regulatory Capital & Financial Resource Adequacy
tags: ["regulatory-capital", "net-capital-rule", "sec-15c3-1", "basel-iii", "fca-ifpr", "capital-adequacy", "financial-compliance"]
brokers_frameworks: ["SEC Rule 15c3-1 (Net Capital)", "Basel III Capital Framework", "FCA IFPR Prudential Rules", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when managing capital requirements for broker-dealers, proprietary trading firms, or regulated financial entities subject to prudential capital rules (e.g. SEC Rule 15c3-1 Net Capital Rule, Basel III, FCA IFPR). Trading firms must maintain net liquid capital above minimum regulatory thresholds plus variable risk-weighted add-ons at all times. Falling below minimum capital mandates immediate business cessation and regulatory notification. This engine computes net available capital, evaluates headroom against regulatory minimums and warning buffers (120%), and generates compliance status reports.

## Prerequisites

- Capital components (`liquid_assets`, `illiquid_deductions`, `total_liabilities`, `subordinated_debt`).
- Regulatory specification (`jurisdiction`, `base_minimum_capital`, `variable_risk_weighted_req`, `warning_buffer_pct`).

## Workflow

1. **Net Capital Calculation**:
   - Compute Net Capital: $\text{Net Capital} = (\text{Liquid Assets} + \text{Subordinated Debt}) - (\text{Total Liabilities} + \text{Illiquid Deductions})$.
2. **Total Requirement Evaluation**:
   - Calculate Total Required: $\text{Total Required} = \text{Base Minimum} + \text{Variable Risk-Weighted Req}$.
3. **Headroom & Ratio Assessment**:
   - Calculate Headroom: $\text{Net Capital} - \text{Total Required}$.
   - Calculate Capital Ratio: $\frac{\text{Net Capital}}{\text{Total Required}}$.
4. **Status & Warning Buffer Classification**:
   - `CAPITAL_DEFICIT` if Net Capital < Total Required.
   - `WARNING_BUFFER_BREACHED` if Total Required $\le$ Net Capital < $1.20 \times \text{Total Required}$.
   - `COMPLIANT` if Net Capital $\ge 1.20 \times \text{Total Required}$.
5. **Report Generation**: Output structured `CapitalStatusReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Including Illiquid Assets in Net Capital**: Treating illiquid real estate, fixed assets, or unlisted equity as liquid capital.
- **Ignoring Risk-Weighted Variable Requirements**: Tracking only base minimum capital while ignoring variable market/credit risk add-ons.
- **No Early Warning Buffer**: Failing to set warning alerts before breaching regulatory capital, leaving no time for capital injection.

## Verification

- Instantiate `RegulatoryCapitalTrackerEngine`. Feed components yielding $550k net capital against $300k requirement ($360k warning threshold) $\implies$ verify `COMPLIANT`. Feed components yielding $320k net capital $\implies$ verify `WARNING_BUFFER_BREACHED`. Feed $150k net capital $\implies$ verify `CAPITAL_DEFICIT`.
- Run `python scripts/test_regulatory_capital_tracker.py`.

## Related Skills

- `broker-account-margin-call-handling`
- `record-retention-periods-by-jurisdiction`
---
