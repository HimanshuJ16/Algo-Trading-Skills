---
name: esg-data-signal-research-and-vendor-comparison
description: >-
  Quantitative alternative data engine for normalizing cross-vendor ESG ratings (MSCI, Sustainalytics, Refinitiv), calculating consensus scores and vendor disagreement dispersion, and generating ESG factor overlay signals.
domain: Quantitative Research & Alternative Data
subdomain: ESG Data & Factor Investing
tags: ["esg-data", "alternative-data", "msci-esg", "sustainalytics", "refinitiv-esg", "vendor-reconciliation", "greenwashing-risk"]
brokers_frameworks: ["MSCI ESG Data", "Sustainalytics Risk API", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in sustainable quantitative investing, multi-factor portfolio construction, and ESG risk overlay models. ESG ratings across major data providers (MSCI, Sustainalytics, Refinitiv) exhibit low cross-vendor correlation ($r \approx 0.30 - 0.50$). This module normalizes vendor-specific scales, computes a multi-vendor consensus ESG score, quantifies vendor disagreement dispersion ($\sigma_{\text{esg}}$), and detects greenwashing or rating noise.

## Prerequisites

- Security identifier (`ticker`, `isin`, `sector`).
- Raw vendor ESG data scores (e.g. MSCI letter rating `'AAA'`, Sustainalytics risk score `15.0`, Refinitiv score `82.0`).
- Exclusion sector flags (`has_controversial_weapons`: True/False).

## Workflow

1. **Vendor Score Normalization**:
   - MSCI: Convert AAA-CCC scale to $[0.0, 1.0]$ percentile score ($\text{AAA}=1.0, \text{CCC}=0.0$).
   - Sustainalytics: Convert risk score to $[0.0, 1.0]$ inverse scale ($1.0 - \frac{\text{Risk}}{100}$).
   - Refinitiv: Scale 0-100 to $[0.0, 1.0]$ ($\frac{\text{Score}}{100}$).
2. **Consensus & Dispersion Calculation**:
   - $\text{Consensus Score} = \bar{S} = \frac{1}{K} \sum S_k$.
   - $\text{Vendor Dispersion} = \sigma_{\text{esg}} = \sqrt{\frac{1}{K} \sum (S_k - \bar{S})^2}$.
   - If $\sigma_{\text{esg}} > 0.25 \implies$ Flag `HIGH_VENDOR_DISAGREEMENT`.
3. **Exclusion & Trading Signal Emission**:
   - If `has_controversial_weapons` $\implies$ Flag `EXCLUDED_SECTOR`.
   - If $\bar{S} \ge 0.75$ and $\sigma_{\text{esg}} \le 0.20 \implies$ Flag `BULLISH_ESG_LEADER`.
   - If $\bar{S} \le 0.30 \implies$ Flag `BEARISH_ESG_LAGGARD`.
4. **Audit Report Generation**: Output structured `EsgSignalAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Naively Averaging Un-Normalized Ratings**: Averaging MSCI letter ratings directly with Sustainalytics risk numbers, corrupting composite factor scores.
- **Ignoring Vendor Disagreement**: Treating companies with high vendor disagreement ($\sigma_{\text{esg}} > 0.25$) as confident ESG leaders, absorbing subjective vendor rating noise.
- **Survivorship Bias in Historical Data**: Using current ESG vendor coverage universes to backtest historical strategy performance.

## Verification

- Instantiate `EsgDataSignalEngine`. Input MSCI `'AAA'` ($1.0$), Sustainalytics `15.0` ($0.85$), Refinitiv `85.0` ($0.85$). Compute consensus score ($0.90$) and low dispersion ($0.07$). Verify engine emits `BULLISH_ESG_LEADER`. Submit conflicting ratings (MSCI `'AAA'` vs Sustainalytics `60.0`). Verify engine flags `HIGH_VENDOR_DISAGREEMENT`.
- Run `python scripts/test_esg_data_signal_research_and_vendor_comparison.py`.

## Related Skills

- `alternative-data-vendor-due-diligence-checklist`
- `data-vendor-cross-validation-for-backtests`
---
