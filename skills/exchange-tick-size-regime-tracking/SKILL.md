---
name: exchange-tick-size-regime-tracking
description: >-
  Quantitative market data engine for tracking dynamic exchange tick size regimes (US SEC Rule 612, EU MiFID II RTS 11, DFM 2026 AED), aligning prices to valid tick steps, and auditing order tick compliance.
domain: Data Management Global
subdomain: Exchange Tick Rules & Price Increments
tags: ["tick-size", "exchange-rules", "mifid-ii-rts-11", "sec-rule-612", "dfm-tick", "price-alignment", "market-data"]
brokers_frameworks: ["SEC Rule 612 Reg NMS", "MiFID II RTS 11", "DFM/ADX Rules", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in quantitative market data pipelines, Smart Order Routers (SOR), and algorithmic order entry engines. Different global exchanges enforce dynamic price-band dependent tick size regimes (e.g. US SEC Rule 612 sub-penny rules for $P < \$1.00$, EU MiFID II RTS 11 liquidity/price band tables, DFM 2026 AED step bands). Submitting off-tick orders triggers immediate venue rejection. This module tracks regime rules, calculates active tick sizes, and aligns order prices to valid tick steps.

## Prerequisites

- Exchange venue identifier (`venue_id`: `'US_EQUITIES'`, `'EU_XETRA'`, `'DFM_DUBAI'`).
- Asset symbol & price ($P$).
- European ADNT liquidity band (if applicable for EU RTS 11).

## Workflow

1. **Active Tick Size Lookup**:
   - Evaluate price $P$ against venue price band rules:
     - `US_EQUITIES`: $P \ge \$1.00 \implies \$0.01$, $P < \$1.00 \implies \$0.0001$.
     - `EU_XETRA`: $P < €10 \implies €0.001$, $€10 \le P < €50 \implies €0.005$, $P \ge €50 \implies €0.01$.
     - `DFM_DUBAI`: $P < 1.00 \implies 0.001$, $1.00 \le P < 10.00 \implies 0.01$, $10.00 \le P < 50.00 \implies 0.02$, $P \ge 50.00 \implies 0.05$.
2. **Price Alignment & Rounding**:
   - $P_{\text{aligned}} = \text{round}\left(\frac{P}{\text{tick}}\right) \times \text{tick}$.
3. **Tick Compliance Audit**:
   - If $|P - P_{\text{aligned}}| > 1e-6 \implies$ Flag `OFF_TICK_REJECTION`.
   - Else $\implies$ Flag `TICK_COMPLIANT`.
4. **Audit Report Generation**: Output structured `TickRegimeAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Applying US Sub-Penny Rules to $P \ge \$1.00$ Shares**: Submitting $\$150.005$ on US equities, breaching SEC Rule 612 and causing venue order rejection.
- **Ignoring European RTS 11 Dynamic Price Bands**: Hardcoding $€0.01$ tick steps across all European equities, missing sub-cent ticks (€0.001 / €0.005) for lower-priced stocks.
- **Floating Point Rounding Errors**: Using raw floating-point division without explicit rounding, misclassifying valid on-tick prices as off-tick.

## Verification

- Instantiate `ExchangeTickSizeRegimeEngine`. Query US Equities @ \$150.00 $\implies$ verify tick = \$0.01. Query US Equities @ \$0.50 $\implies$ verify tick = \$0.0001. Query EU Xetra @ €25.00 $\implies$ verify tick = €0.005. Submit off-tick price (\$150.005 on US Equities). Verify engine flags `OFF_TICK_REJECTION` and provides aligned price (\$150.01).
- Run `python scripts/test_exchange_tick_size_regime_tracking.py`.

## Related Skills

- `minimum-fill-size-and-lot-rounding-logic`
- `deutsche-borse-xetra-api-integration`
---
