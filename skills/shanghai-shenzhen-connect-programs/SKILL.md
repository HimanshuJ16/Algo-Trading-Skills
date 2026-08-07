---
name: shanghai-shenzhen-connect-programs
description: >-
  Production-grade Shanghai-Shenzhen Stock Connect (Northbound Trading) Engine enforcing RMB 52 billion daily quota tracking, T+1 day-trading restrictions, 100-share round lot board sizes, and CNH currency settlement for SSE and SZSE A-shares.
domain: Global Exchange Connectivity & Cross-Border Trading
subdomain: China Stock Connect & Northbound Trading
tags: ["stock-connect", "shanghai-connect", "shenzhen-connect", "northbound-trading", "t-plus-1-settlement", "daily-quota"]
brokers_frameworks: ["HKEX Stock Connect Rules", "SSE / SZSE Trading Rules", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when routing Northbound equity orders from Hong Kong / international institutions into Mainland China A-shares listed on the Shanghai Stock Exchange (SSE) or Shenzhen Stock Exchange (SZSE) via Stock Connect. Northbound trading operates under strict regulatory constraints: RMB 52 billion daily quota limit per channel (SSE / SZSE), mandatory T+1 settlement rule prohibiting day trading (shares bought on day $T$ cannot be sold until $T+1$), 100-share round lot board sizes for buy orders, and CNH currency settlement.

## Prerequisites

- Order payload (`ConnectOrder`: `order_id`, `symbol`, `channel`: `SHANGHAI_CONNECT` or `SHENZHEN_CONNECT`, `side`, `quantity`, `price_cnh`, `order_date_iso`, `purchased_date_iso`).
- Daily quota limit (RMB 52 billion per channel).

## Workflow

1. **Board Lot Validation (100-share multiples)**:
   - Verify buy order quantity is a multiple of 100 shares ($\text{qty} \bmod 100 == 0$).
2. **T+1 Day-Trading Prohibition Check**:
   - For sell orders, check `purchased_date_iso`: if `purchased_date_iso == order_date_iso`, reject order (`TPLUS1_VIOLATION`).
3. **Northbound Daily Quota Inspection**:
   - For buy orders, verify order notional $\le \text{remaining\_quota\_cnh}$. Deduct notional upon execution. Sell orders restore quota balance.
4. **Execution Output**: Output structured `ConnectExecutionResult`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Attempting Day-Trading (T+0 Selling)**: Trying to sell A-shares on the same day they were purchased, causing HKEX/Mainland exchange trade rejection.
- **Odd Lot Buy Submissions**: Submitting Northbound buy orders for fractional or non-100-share quantities (only sell orders are permitted to unload odd-lot remnants).
- **Ignoring Daily Quota Exhaustion**: Continuing to route buy orders after the RMB 52 billion daily quota balance drops to zero during continuous auction.

## Verification

- Instantiate `ShanghaiShenzhenConnectEngine`. Route valid 100-share buy order for Moutai (`600519.SH`) $\implies$ verify `is_executed=True` and RMB 170,000 quota deducted. Route same-day sell order $\implies$ verify `TPLUS1_VIOLATION` rejection. Route 150-share buy order $\implies$ verify `INVALID_BOARD_LOT` rejection. Route buy order when quota is exhausted $\implies$ verify `QUOTA_EXCEEDED` rejection.
- Run `python scripts/test_shanghai_shenzhen_connect_programs.py`.

## Related Skills

- `shanghai-shenzhen-connect-programs`
- `multi-currency-pnl-and-fx-conversion`
---
