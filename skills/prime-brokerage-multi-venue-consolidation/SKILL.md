---
name: prime-brokerage-multi-venue-consolidation
description: >-
  Prime brokerage multi-venue trade consolidation engine generating central give-up clearing payloads, netting position exposures, and optimizing cross-margining across venues.
domain: Settlement & Post-Trade Operations
subdomain: Prime Brokerage & Multi-Venue Clearing
tags: ["prime-brokerage", "give-up-trades", "multi-venue", "clearing-consolidation", "net-margining", "post-trade", "settlement"]
brokers_frameworks: ["Traiana / FIX Give-Up Protocol", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when institutional quantitative trading strategies execute across multiple brokers, ECNs, lit exchanges, and dark pools. Clearing and settling trades directly with dozens of executing brokers traps cash in duplicated margin pools and creates severe post-trade reconciliation overhead. This engine consolidates multi-venue trade fills, generates Prime Broker (PB) give-up clearing instructions, nets symbol positions, and computes consolidated margin savings.

## Prerequisites

- Executed trade records across venues (`execution_id`, `executing_broker`, `venue_id`, `symbol`, `side`: `'BUY'`/`'SELL'`, `quantity`, `price`, `trade_date`).
- Prime Broker specification (`prime_broker_name`, `pb_account_id`, `clearing_fee_per_contract`).

## Workflow

1. **Multi-Venue Execution Ingestion**:
   - Collect trade fills executed across distinct brokers/venues.
2. **Position Netting & Gross Volume Calculation**:
   - Compute net position per symbol: $\text{NetPos} = \sum_{\text{BUY}} Q - \sum_{\text{SELL}} Q$.
   - Compute total gross notional volume: $V_{\text{gross}} = \sum (Q_i \cdot P_i)$.
3. **Consolidated Margin & Give-Up Payload Generation**:
   - Compute consolidated net margin savings versus fragmented standalone broker margins.
   - Format give-up clearing record for Prime Broker batch upload.
4. **Audit Report Generation**: Output structured `PBConsolidationReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Unmatched Give-Up Trades**: Failing to transmit give-up messages to the Prime Broker within the T+0 cutoff window, causing trade breaks.
- **Ignoring Execution Broker Fees**: Forgetting to account for third-party executing broker commissions in addition to PB clearing fees.
- **Duplicate Fill Submissions**: Re-submitting execution fills into the PB give-up queue upon network reconnects.

## Verification

- Instantiate `PrimeBrokerageMultiVenueConsolidationEngine`. Ingest 2 trades for `AAPL` (BUY $1,000$ shares @ $\$150$ via Broker A; SELL $400$ shares @ $\$151$ via Broker B). Verify net position $= +600$ shares, gross traded value $= \$210,400$, give-up payload generated, and margin savings calculated.
- Run `python scripts/test_prime_brokerage_multi_venue_consolidation.py`.

## Related Skills

- `broker-account-margin-call-handling`
- `multi-source-price-reconciliation-tie-breaking`
---
