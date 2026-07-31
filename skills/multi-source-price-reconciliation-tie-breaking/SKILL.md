---
name: multi-source-price-reconciliation-tie-breaking
description: >-
  Multi-source price reconciliation engine filtering outlier vendor quotes, evaluating tolerance bounds, and applying deterministic tie-breaking rules.
domain: Data Management Global
subdomain: Multi-Vendor Price Reconciliation & Data Quality
tags: ["price-reconciliation", "tie-breaking", "multi-vendor", "golden-source", "outlier-filtering", "canonical-price", "data-quality"]
brokers_frameworks: ["Golden Source Orchestration", "Median Absolute Deviation", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when ingesting real-time market data quotes from multiple vendors (e.g. Bloomberg, Refinitiv/LSEG, Polygon, ICE Data, Binance, Coinbase) for the same security. Heterogeneous feed latencies, stale connections, or bad quotes create pricing discrepancies across vendors. This engine filters outlier quotes using median deviation bounds ($|P_i - M| / M > 1.0\%$), assesses quote agreement within tight tolerance thresholds ($0.05\%$), and applies deterministic tie-breaking rules (`PRIORITY`, `FRESHNESS`, `VOLUME_WEIGHTED`) to output a single canonical benchmark price.

## Prerequisites

- Vendor price quotes (`vendor_id`, `symbol`, `price`, `timestamp`, `volume_depth`, `vendor_priority`, `reliability_weight`).
- Reconciliation config (`max_deviation_pct`: e.g. 0.01, `tolerance_pct`: e.g. 0.0005, `tie_breaker_method`: `'PRIORITY'`, `'FRESHNESS'`, `'VOLUME_WEIGHTED'`).

## Workflow

1. **Outlier Quote Filtering**:
   - Calculate median price $M = \text{median}(\{P_i\})$.
   - Flag and discard quotes deviating from median by $> \text{max\_deviation\_pct}$:
     $$\frac{|P_i - M|}{M} > \delta_{\text{max}}$$
2. **Tolerance Audit & Composite Pricing**:
   - Check if valid quotes agree within tolerance $\delta_{\text{tol}}$:
     $$\frac{\max(P_i) - \min(P_i)}{M} \le \delta_{\text{tol}}$$
     If satisfied $\implies$ Compute reliability-weighted average: $P_{\text{canonical}} = \frac{\sum w_i P_i}{\sum w_i}$.
3. **Deterministic Tie-Breaking Protocol**:
   - If quotes conflict beyond tolerance, execute tie-breaking logic:
     - **PRIORITY**: Select quote from vendor with highest priority rank ($\min r_i$).
     - **FRESHNESS**: Select quote with most recent timestamp ($\max t_i$).
     - **VOLUME_WEIGHTED**: Select quote with largest market volume depth ($\max V_i$).
4. **Audit Report Generation**: Output structured `PriceReconciliationReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Propagating Outlier Spikes**: Calculating unweighted arithmetic averages across all vendors without filtering bad outlier quotes first.
- **Non-Deterministic Tie-Breaking**: Relying on arbitrary hash-map key iteration order, leading to non-reproducible pricing decisions across runs.
- **Ignoring Timestamp Latency**: Treating stale vendor quotes (seconds old) equal to fresh zero-latency feeds.

## Verification

- Instantiate `MultiSourcePriceReconcilerEngine`. Input quotes from Bloomberg ($100.00$, priority 1), Refinitiv ($100.02$, priority 2), and Polygon ($105.00$, bad outlier) $\implies$ verify $105.00$ is filtered out as an outlier, and Bloomberg/Refinitiv are reconciled to canonical price $100.01$.
- Run `python scripts/test_price_reconciliation.py`.

## Related Skills

- `vendor-outage-fallback-data-source-hierarchy`
- `cross-vendor-timestamp-precision-reconciliation`
---
