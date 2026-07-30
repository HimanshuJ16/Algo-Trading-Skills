---
name: cme-globex-futures-api-integration
description: Quantitative market connectivity module for CME Globex futures order
  entry, enforcing Tag 50 (Operator ID) Rule 576 compliance, Market-With-Protection
  (MWP) limits, and price banding.
domain: Market Connectivity
subdomain: Exchange API
tags:
- cme-globex
- ilink3
- futures
- tag50
- mwp
- price-banding
brokers_frameworks:
- CME Globex iLink 3
- CME FIX
version: 1.0.0
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when connecting trading algorithms to CME Globex for trading futures and options contracts (e.g., E-mini S&P 500 `ES`, Crude Oil `CL`, Treasury Futures `ZB`). CME Globex requires strict adherence to **CME Rule 576** (mandatory Tag 50 / Operator ID on every message) and enforces **Price Banding** and **Market-With-Protection (MWP)** to prevent rogue orders from disturbing the market.

## Prerequisites

- CME Globex Firm ID, Session ID, and registered Operator IDs (Tag 50).
- Contract specification parameters (tick size, contract multiplier, price band limits).

## Workflow

1. **Order Initialization**: Instantiate `CmeGlobexOrder` with required CME tags: `symbol`, `side`, `quantity`, `order_type`, `operator_id` (Tag 50), and `account`.
2. **Pre-Trade Risk Check**:
   - **Tag 50 Audit**: Validate that `operator_id` is present and formatted according to Rule 576 (2-18 alphanumeric characters).
   - **Price Banding Check**: Compare the limit price against the reference market price. Reject orders outside the CME price band.
3. **Market-With-Protection (MWP) Conversion**: If submitting a Market order, automatically convert it to a `Limit` order set at the current BBO plus/minus the exchange-defined Protection Point threshold.
4. **Message Formatting**: Format order into iLink 3 / FIX `NewOrderSingle` message structure.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Missing Tag 50 (Rule 576 Breach)**: Submitting messages without a valid Operator ID. CME will reject the session or fine the firm.
- **Unprotected Market Orders**: Submitting a un-capped Market order. CME Globex automatically applies Market-With-Protection (MWP); if your client does not account for MWP, partial fills at the protection limit will leave unexecuted shares as resting limit orders unexpectedly.
- **Fat-Finger Price Band Violations**: Sending limit orders outside the CME price band limit, resulting in immediate `SessionReject` or `ExecutionReport(REJECTED)` messages.

## Verification

- Initialize `CmeGlobexOrderEngine`. Attempt to place a market order without a Tag 50 (Operator ID). Verify immediate rejection. Place a valid Market order and verify it converts to a Market-With-Protection limit order bounded by the configured protection ticks.
- Run `python scripts/test_cme_globex_futures_api_integration.py`.

## Related Skills

- `canada-iiroc-electronic-trading-rules`
- `fix-protocol-session-management-across-venues`
