---
name: insider-transaction-filing-signal-research
description: >-
  Quantitative factor research engine for SEC Form 4 insider transaction filings, isolating opportunistic non-10b5-1 C-suite open-market purchases from routine scheduled sales.
domain: Quant Research & Alt Data
subdomain: Insider Filing Analytics & Factor Research
tags: ["insider-trading-signal", "sec-form-4", "rule-10b5-1", "opportunistic-trading", "alpha-factors", "insider-sentiment"]
brokers_frameworks: ["SEC EDGAR Form 4", "Cohen-Malloy-Pomorski (2012) Factor Research", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when researching corporate insider trading alpha signals and building equity factor strategies based on SEC Form 4 filings. Corporate insiders (CEOs, CFOs, Directors) possess superior information regarding company fundamentals. However, the majority of insider sales are pre-arranged routine transactions executed under **Rule 10b5-1 trading plans**, which carry zero predictive alpha. This module isolates **Opportunistic Non-10b5-1 Open-Market Purchases** by C-suite executives, weighting trade notionals by executive role ($w_{\text{CEO}}=1.0$ vs $w_{\text{Director}}=0.6$) to compute net insider sentiment.

## Prerequisites

- SEC Form 4 transaction event data (`insider_name`, `role`: `CEO`/`CFO`/`DIRECTOR`/`TEN_PCT_OWNER`, `transaction_code`: 'P' Purchase / 'S' Sale, `shares`, `price`, `is_rule_10b5_1`, `is_open_market`).
- Role weighting schedule ($w_{\text{CEO/CFO}} = 1.0$, $w_{\text{Director}} = 0.6$, $w_{\text{10% Owner}} = 0.3$).

## Workflow

1. **Form 4 Transaction Ingestion & Filtering**:
   - Ingest Form 4 transaction records.
   - Filter out pre-arranged Rule 10b5-1 trades (`is_rule_10b5_1 == True`) and non-open-market option exercises (`is_open_market == False`) as `ROUTINE_PREARRANGED`.
2. **Executive Role Weighting**:
   - Assign role weight $w$:
     - `CEO` / `CFO` $\implies w = 1.0$.
     - `DIRECTOR` $\implies w = 0.6$.
     - `TEN_PCT_OWNER` $\implies w = 0.3$.
3. **Role-Weighted Net Insider Sentiment Calculation**:
   - Weighted Notional $= \text{Sign} \times w \times (\text{shares} \times \text{price})$.
   - Compute Normalized Sentiment Score $S_{\text{insider}} \in [-1.0, +1.0]$.
4. **Signal Classification**:
   - $S_{\text{insider}} \ge +0.3 \implies$ `STRONG_BULLISH_OPPORTUNISTIC_BUY`.
   - $S_{\text{insider}} \le -0.3 \implies$ `BEARISH_OPPORTUNISTIC_SELL`.
   - Otherwise $\implies$ `NEUTRAL_ROUTINE`.
5. **Audit Report Generation**: Output structured `InsiderFilingSignalReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating Rule 10b5-1 Sales as Bearish Signals**: Shorting stock based on automated 10b5-1 scheduled insider sales, which reflect routine diversification rather than negative fundamental sentiment.
- **Equal Weighting 10% Owners and CEOs**: Treating 10% beneficial hedge fund owners equal to internal CEOs who possess direct operational visibility.
- **Ignoring Filing Delay Lags**: Failing to account for the 2-business-day SEC Form 4 filing window lag when backtesting alpha signals.

## Verification

- Instantiate `InsiderFilingSignalEngine`. Audit CEO Open-Market Purchase (10,000 shares @ $50.00 = $500k, `is_rule_10b5_1=False`) $\implies$ verify engine classifies `STRONG_BULLISH_OPPORTUNISTIC_BUY` ($S = +1.00$). Audit Automated 10b5-1 Sale $\implies$ verify engine filters trade as `ROUTINE_PREARRANGED` ($S = 0.00$).
- Run `python scripts/test_insider_transaction_filing_signal_research.py`.

## Related Skills

- `insider-trading-controls-for-alternative-data-usage`
- `earnings-call-transcript-nlp-signal-research`
---
