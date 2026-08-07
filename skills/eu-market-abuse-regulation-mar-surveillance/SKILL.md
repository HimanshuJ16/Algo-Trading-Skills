---
name: eu-market-abuse-regulation-mar-surveillance
description: Quantitative trade surveillance engine for detecting EU Market Abuse
  Regulation (MAR - Regulation 596/2014) patterns (spoofing/layering, wash trading,
  quote stuffing) and generating STOR regulatory filings.
domain: Trade Surveillance & Regulatory Compliance
subdomain: Market Abuse & STOR Reporting (EU MAR)
tags:
- eu-mar
- market-abuse
- trade-surveillance
- stor-report
- spoofing-detection
- wash-trading
- quote-stuffing
brokers_frameworks:
- EU MAR Regulation 596/2014
- ESMA RTS 25 STOR
- Python Dataclasses
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in European quantitative fund compliance, broker trade surveillance systems, and algorithmic risk monitoring. Under **EU Market Abuse Regulation (MAR - Regulation (EU) No 596/2014 Article 16)**, firms executing orders in European markets must establish automated surveillance to detect market manipulation patterns (Spoofing/Layering, Wash Trading, Quote Stuffing) and file **Suspicious Transaction and Order Reports (STOR)** to National Competent Authorities (NCAs) without delay.

## Prerequisites

- Trade and order event logs (`cl_ord_id`, `isin`, `side`, `price`, `qty`, `timestamp_ns`, `buyer_account_id`, `seller_account_id`, `order_status`).
- MAR detection parameters (`cancel_ratio_threshold`: 0.90, `quote_rate_threshold`: 500 msgs/sec).

## Workflow

1. **Wash Trading Detection**:
   - Audit fill events where `buyer_account_id == seller_account_id` $\implies$ Flag `WASH_TRADE_ALERT`.
2. **Spoofing & Layering Pattern Detection**:
   - Audit order cancellation ratio ($\frac{N_{\text{cancels}}}{N_{\text{orders}}} > 90\%$) with rapid order lifetimes ($< 100\text{ms}$) on one side followed by execution on the opposite side $\implies$ Flag `SPOOFING_ALERT`.
3. **Quote Stuffing Burst Audit**:
   - Measure order message rate per second ($> 500\text{ msgs/sec}$) $\implies$ Flag `QUOTE_STUFFING_ALERT`.
4. **STOR Regulatory Report Generation**:
   - Format ESMA RTS 25 STOR report for submission to NCAs (BaFin, AMF, FCA).
5. **Audit Report Generation**: Output structured `EuMarSurveillanceAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring Order Cancellations in Surveillance**: Monitoring filled trades only, missing non-executed spoofing and layering order patterns.
- **Failing to File STOR Reports Promptly**: Delaying STOR submissions beyond the "without delay" regulatory window after detecting suspicious activity.
- **Conflating Market Making with Quote Stuffing**: Setting quote rate thresholds too low, flagging legitimate high-frequency market making quotes as market abuse.

## Verification

- Instantiate `EuMarSurveillanceEngine`. Submit trade execution with matching buyer/seller account IDs. Verify engine detects `WASH_TRADE_ALERT` and generates STOR filing data. Submit high cancellation spoofing stream ($95\%$ cancel rate, $50\text{ms}$ order lifespan). Verify engine flags `SPOOFING_ALERT`.
- Run `python scripts/test_eu_market_abuse_regulation_mar_surveillance.py`.

## Related Skills

- `cross-venue-latency-arbitrage-defensive-design`
- `order-to-trade-ratio-fee-penalty-avoidance`
---
