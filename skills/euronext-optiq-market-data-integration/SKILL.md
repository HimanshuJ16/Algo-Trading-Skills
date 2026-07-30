---
name: euronext-optiq-market-data-integration
description: Quantitative venue integration engine for parsing Euronext Optiq Market
  Data Gateway (MDG) Simple Binary Encoding (SBE) multicast feeds, reconstructing
  L2 order book depth, and monitoring trading state transitions.
domain: Venue Integration & Protocols
subdomain: European Market Data (Euronext Optiq)
tags:
- euronext
- optiq-mdg
- sbe-binary
- l2-order-book
- multicast-feed
- lvmh
- asml
- market-microstructure
brokers_frameworks:
- Euronext Optiq MDG
- SBE Protocol
- Python Dataclasses
version: 1.0.0
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in European quantitative trading systems, high-frequency execution algorithms, and market making bots connecting to Euronext exchanges (Paris, Amsterdam, Brussels, Lisbon, Milan, Dublin). Euronext operates **Optiq Market Data Gateway (MDG)**, disseminating real-time order book updates over UDP multicast using **Simple Binary Encoding (SBE)**. This module decodes binary SBE packets, maintains L2 order book depth, calculates order book imbalance, and detects trading halt transitions.

## Prerequisites

- Optiq Symbol ID / ISIN (e.g. `FR0000121014` - LVMH, `NL0010273215` - ASML Holding).
- Multicast feed A/B stream configuration.

## Workflow

1. **Optiq SBE Binary Frame Parsing**:
   - Unpack SBE header (`msg_type: 1001` MarketUpdate, `1004` Trade, `1005` SymbolStatus, `sequence_number`, `book_in_ns`).
2. **L2 Order Book Maintenance**:
   - Process book updates (`ADD`, `MODIFY`, `DELETE`) across Bid and Ask levels.
3. **Microstructure Signal Computation**:
   - $\text{Mid Price} = \frac{P_{\text{bid1}} + P_{\text{ask1}}}{2.0}$.
   - $\text{Book Imbalance} = \frac{V_{\text{bid1}} - V_{\text{ask1}}}{V_{\text{bid1}} + V_{\text{ask1}}}$.
4. **Trading State Change Monitoring**:
   - If `msg_type: 1005` indicates `HALTED_CIRCUIT_BREAKER` or `CALL_AUCTION` $\implies$ Flag `TRADING_HALTED` state to halt quoting.
5. **Audit Report Generation**: Output structured `OptiqMarketDataAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring SBE Line Arbitration**: Processing a single UDP multicast line without line A/B arbitration, missing dropped packets during bursty market events.
- **Quoting During Auction Halts**: Continuing to send limit orders when Optiq broadcasts a `SymbolStatus` transition to `CALL_AUCTION` or `HALTED`.
- **Mis-Scaling Price Decimals**: Failing to apply Optiq decimal scale factors (e.g. price raw integer $7850000 \to €785.00$).

## Verification

- Instantiate `EuronextOptiqMarketDataEngine`. Ingest Optiq MarketUpdate SBE packet for `FR0000121014` (LVMH: Bid 785.00 @ 500 qty, Ask 785.50 @ 200 qty). Verify engine reconstructs L2 book, computes Mid = €785.25, Spread = €0.50, Imbalance = +0.4286. Ingest `SymbolStatus` packet transitioning to `HALTED`. Verify engine updates state to `TRADING_HALTED`.
- Run `python scripts/test_euronext_optiq_market_data_integration.py`.

## Related Skills

- `deutsche-borse-xetra-api-integration`
- `eurex-market-data-and-order-api`
---
