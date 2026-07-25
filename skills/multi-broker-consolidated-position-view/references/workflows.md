# Deep Workflow Reference — multi-broker-consolidated-position-view

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Raw Position Ingestion & Symbol Mapping**:
   - Ingest raw position lists from all broker adapters (`broker_name`, `broker_symbol`, `qty`, `avg_cost`, `current_price`, `currency`).
   - Translate broker-specific tickers into canonical symbols.

2. **Currency Conversion to USD**:
   - Convert market value and cost basis to USD using point-in-time FX rates.

3. **Consolidation & Netting**:
   - Calculate net quantity $Q_{\text{net}} = \sum_b Q_b$ and gross quantity $Q_{\text{gross}} = \sum_b |Q_b|$.
   - Aggregate total USD market value, weighted cost basis, and unrealized P&L.

4. **Reconciliation Audit**:
   - Compare consolidated broker holdings against internal target strategy ledger (`reconcile_against_target()`).

## Production Implementation Reference

- Reference code: `scripts/consolidated_ledger.py` (`MultiBrokerConsolidatedLedger`, `RawBrokerPosition`, `ConsolidatedPosition`).
- Automated unit tests: `scripts/test_consolidated_ledger.py`.
