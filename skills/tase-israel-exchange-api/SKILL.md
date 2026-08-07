---
name: tase-israel-exchange-api
description: "Institutional connectivity engine for the Tel Aviv Stock Exchange (TASE), supporting FIX 5.0 SP2 session management, Agorot-ILS currency scaling, Sunday-Thursday trading calendar compliance, and pre-trade risk controls."
domain: Execution
subdomain: Venue Integration
tags:
- tase
- israel
- fix-protocol
- order-routing
- market-data
- agorot-conversion
- sunday-trading
brokers_frameworks:
- quickfix
- tase-data-hub
version: "1.1.0"
author: Quant Engineering
license: MIT
---

## When to Use

Use this skill when building Direct Market Access (DMA), order routing, algorithmic execution, or market data integration with the **Tel Aviv Stock Exchange (TASE)**.

This skill provides institutional-grade mechanisms for:
- Connecting to the TASE FIX 4.4 / FIX 5.0 SP2 Gateway for order submission, cancellation, and execution processing.
- Managing TASE's unique **Sunday-Thursday trading calendar** and session phases (Pre-Open, Opening Auction, Continuous Trading, Closing Auction).
- Handling price denomination conversions between **Agorot** (cents, used for Equities/Mutual Funds where 100 Agorot = 1 ILS) and **ILS/NIS** (used for Bonds, T-Bills/Makam, and Index Derivatives).
- Enforcing pre-trade risk controls (max order value in ILS, max order quantity, and reference price collar validation).

## Prerequisites

- Python 3.9+
- Network connectivity to TASE FIX Gateways (Co-Location or certified extranet/VPN).
- Assigned `SenderCompID`, `TargetCompID`, and `TraderID` credentials from TASE Member Services.
- ISIN and TASE 6/7-digit Security Identifier mapping from TASE Data Hub or MAYA API.

## Workflow

1. **Initialize Engine & Config**: Configure `TASEConfig` with session IDs (`sender_comp_id`, `target_comp_id`), host, port, trader ID, and risk thresholds (`max_order_value_ils`, `max_price_collar_pct`).
2. **Register Security Metadata**: Register target securities using `TASESecurity` with exact symbol (e.g. `TEVA.TA`), ISIN (`IL0001082511`), price denomination (`AGOROT` vs `ILS`), and reference prices.
3. **Session Logon**: Call `connect()` to initiate FIX Logon (MsgType=A) and begin heartbeat monitoring.
4. **Trading Calendar & Phase Verification**: Invoke `get_market_phase()` to verify the trading state in Israel Standard Time (IST). Note: TASE trades Sunday through Thursday; Friday and Saturday are non-trading weekend days.
5. **Order Submission & Risk Validation**: Construct `TASEOrder` objects (specifying `BUY`/`SELL`, order type, quantity, limit price in Agorot or ILS). Call `submit_order()` which performs automated pre-trade risk checks before queuing NewOrderSingle (MsgType=D).
6. **Execution Processing**: Process asynchronous `ExecutionReport` (MsgType=8) messages via `simulate_execution_report()` to maintain cumulative filled quantities, average execution price (VWAP), and order status transitions.
7. **Order Cancellation & Disconnect**: Use `cancel_order()` to transmit OrderCancelRequest (MsgType=F) for active orders, and `disconnect()` to send Logout (MsgType=5) upon session end.

## Common Pitfalls

- **Agorot vs. ILS Price Scale Misconfiguration**: Submitting equity orders in ILS instead of Agorot (or vice versa) results in catastrophic 100x over/under-pricing. Always verify `PriceDenomination` (`AGOROT` vs `ILS`).
- **Ignoring Sunday Trading Calendar**: Global automated systems configured strictly for Mon-Fri trading will miss Sunday TASE trading sessions (where ~20% of weekly volume occurs) and attempt invalid order routing on Fridays.
- **Dynamic Price Collar Violations**: TASE matching engine rejects orders exceeding dynamic reference price thresholds. Pre-trade checks must validate orders against the current TASE reference price.
- **Unhandled Session Sequence Numbers**: Failing to persist FIX sequence numbers across intraday reconnects leads to sequence gap rejections and missed execution reports.

## Verification

Execute the test suite to validate currency conversions, risk limits, calendar logic, and execution handling:

```bash
python -m unittest discover -s skills/tase-israel-exchange-api/scripts
```

## Related Skills

- `fix-protocol-session-management-across-venues`
- `global-exchange-holiday-calendar-handling`
- `order-placement-idempotency`
- `kill-switch-and-drawdown-circuit-breakers`

