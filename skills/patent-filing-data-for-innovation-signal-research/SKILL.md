---
name: patent-filing-data-for-innovation-signal-research
description: >-
  Patent filing and innovation signal research engine parsing USPTO/EPO patent velocity, forward citations, and claim counts to compute Innovation Quality Scores (IQS) for equity alpha factors.
domain: Quantitative Research & Alternative Data
subdomain: Corporate Innovation & R&D Alpha Signals
tags: ["patent-data", "uspto", "innovation-signal", "forward-citations", "alt-data", "quant-factor", "r-and-d"]
brokers_frameworks: ["USPTO PatentsView API", "Pandas DataFrames", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when developing long-horizon fundamental and quantitative equity signals based on corporate R&D productivity and patent innovation. Traditional balance-sheet R&D accounting expenses fail to distinguish between productive vs wasteful spending. Parsing patent filing velocity, forward citation counts (which measure technological impact), and patent family breadth generates cross-sectional Innovation Quality Scores ($IQS$) that predict long-term stock outperformance.

## Prerequisites

- Corporate patent filing records (`asset_id`, `filing_date`, `grant_date`, `patent_id`, `forward_citations`, `claim_count`, `technology_class`).
- Configured signal weights (`velocity_weight`: 0.50, `citation_weight`: 0.50).

## Workflow

1. **Patent Velocity & Citation Extraction**:
   - Compute patent filing count $V_i = \text{Count}(\text{Patents}_i)$.
   - Compute logarithmic forward citation impact $C_i = \sum \ln(1 + \text{ForwardCitations}_{i, j})$.
2. **Innovation Quality Score ($IQS$) Calculation**:
   - Compute raw Innovation Quality Score:
     $$IQS_i = (w_{\text{vel}} \cdot V_i) + (w_{\text{cite}} \cdot C_i)$$
3. **Cross-Sectional Z-Score Normalization**:
   - Normalize IQS scores across the asset universe ($Z \in [-3.0, +3.0]$):
     $$Z_i = \frac{IQS_i - \mu_{IQS}}{\sigma_{IQS}}$$
4. **Audit Report Generation**: Output structured `PatentInnovationReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring Filing-to-Grant Availability Lag**: Using grant dates as filing dates, introducing lookahead bias (USPTO patent grants take 18-36 months after application).
- **Failing to Sector-Normalize**: Comparing tech patent volume against consumer staple patent volume without industry sector normalization.
- **Unweighted Citation Counts**: Treating raw citations linearly instead of using logarithmic scaling ($\ln(1 + C)$) to prevent mega-cited outlier patents from distorting factors.

## Verification

- Instantiate `PatentFilingDataForInnovationSignalResearchEngine`. Input DataFrame with 2 assets (`AAPL` with 10 patents + 50 citations vs `MSFT` with 2 patents + 5 citations) $\implies$ verify `AAPL` receives positive Z-score and higher IQS. Evaluate empty DataFrame $\implies$ verify empty list returned.
- Run `python scripts/test_patent_filing_data_for_innovation_signal_research.py`.

## Related Skills

- `insider-transaction-filing-signal-research`
- `earnings-call-transcript-nlp-signal-research`
---
