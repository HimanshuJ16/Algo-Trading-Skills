---
name: multi-broker-consolidated-position-view
description: Use when running quantitative trading strategies across multiple brokers
  to aggregate, reconcile, and net position holdings, market values, and unrealized
  P&L into a single consolidated base-currency risk accounting view.
domain: algorithmic-trading
subdomain: broker-integration
tags:
- broker-integration
- multi-broker
- consolidated-view
- position-reconciliation
- risk-accounting
- netting
brokers_frameworks:
- Multi-Broker Ledger
- Python Risk Engine
version: '1.0'
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill whenever a strategy distributes execution across multiple brokerage accounts or exchanges (e.g., US equities on IBKR and Alpaca, crypto on Binance). Isolated per-broker position views create blind spots in gross exposure and risk limits. This skill normalizes broker-specific symbol formats, converts multi-currency market values into a base currency (USD), nets long/short holdings across brokers, and audits position discrepancies.

## Prerequisites

- Active position feeds or snapshots from each integrated broker adapter.
- Symbol translation map (broker symbol -> canonical symbol).
- Current FX rates table for non-base currency conversions.

## Workflow

1. **Ingest Raw Broker Positions**:
   - Ingest position arrays from each broker adapter (`broker_name`, `broker_symbol`, `qty`, `avg_cost`, `current_price`, `currency`).

2. **Normalize Symbols & Currencies**:
   - Resolve `broker_symbol` to canonical symbol (`AAPL`, `EURUSD`, `BTC`).
   - Convert market value to base currency USD using point-in-time FX rates.

3. **Aggregate & Net Consolidated Position**:
   - For each canonical symbol, compute total net quantity $Q_{\text{net}} = \sum_b Q_b$, gross quantity $Q_{\text{gross}} = \sum_b |Q_b|$, total market value USD, and weighted average entry price.

4. **Reconciliation Audit**:
   - Compare strategy internal target ledger against consolidated broker holdings to flag execution drift or unhedged residual exposure.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Unmatched Symbol Mismatches**: Failing to translate exchange-specific suffixes (e.g. `AAPL.US` vs `AAPL`) resulting in duplicate position entries for the same asset.
- **Ignoring FX Conversion Drift**: Summing non-USD positions (e.g. EUR, JPY) directly into USD market value without FX conversion.
- **Opposing Position Capital Waste**: Holding long 100 AAPL on Broker A and short 100 AAPL on Broker B without netting, wasting margin capital on both sides.

## Verification

- Aggregate positions across 3 brokers (IBKR, Alpaca, Binance) and verify net quantity, gross market value, and FX conversion accuracy.
- Simulate a position mismatch between internal strategy ledger and broker holdings and verify reconciliation alert.
- Run `python scripts/test_consolidated_ledger.py` and confirm 100% pass rate.

## Related Skills

- `multi-asset-backtest-currency-normalization`
- `broker-failover-secondary-account-routing`
- `correlation-aware-exposure-limits`
---
