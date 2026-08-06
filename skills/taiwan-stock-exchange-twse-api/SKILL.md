---
name: taiwan-stock-exchange-twse-api
description: Institutional trading integration with Taiwan Stock Exchange (TWSE), enforcing FINI registration IDs, 10% daily price limits, dynamic tick size regimes, board lot validation, and naked short selling restrictions.
domain: Execution
subdomain: Venue Integration
tags:
- taiwan-stock-exchange
- twse
- fini
- venue-integration
- execution
brokers_frameworks:
- generic
version: 1.0.0
author: System
license: MIT
---

## When to Use

Use this skill when routing orders or integrating institutional market connectivity with the Taiwan Stock Exchange (TWSE). It ensures pre-trade risk compliance for foreign institutional investors (FINI), enforces board lot rules, and prevents regulatory breaches.

## Prerequisites

- Python 3.9+
- Valid TWSE API credentials or broker DMA gateway connection.
- Approved FINI (Foreign Institutional Investor) registration ID.

## Workflow

1. **Initialize Config**: Configure API credentials, FINI registration ID, and daily price limit percentages (default 10%).
2. **Dynamic Tick Size Verification**: Verify that order prices strictly align with TWSE tick increments (NT$0.01 for < NT$50, NT$0.05 for >= NT$50).
3. **Daily Price Limit Compliance**: Check that limit prices do not breach the 10% upper or lower daily limit bounds relative to the previous day's closing price.
4. **Lot Size & Locate Audit**: Enforce 1,000-share regular board lot sizing (unless flagged as intra-day odd lot) and ensure borrow locate availability for short sale orders to prevent naked shorting.
5. **Order Submission**: Route valid orders to the TWSE matching system.

## Common Pitfalls

- **Naked Shorting Penalty**: TWSE strictly prohibits naked short sales. Orders marked `SHORT_SELL` without active borrow locates are rejected.
- **Odd Lot Submission Errors**: Submitting non-1,000 share multiples to standard regular board lot order books causes immediate rejection.
- **Price Limit Breaches**: Orders placed beyond the 10% price limit ceiling/floor will be rejected by exchange pre-trade risk filters.

## Verification

Run the test suite:
```bash
cd skills/taiwan-stock-exchange-twse-api/scripts
python -m unittest test_taiwan_stock_exchange_twse_api.py
```

## Related Skills

- `shanghai-shenzhen-connect-programs`
- `singapore-exchange-sgx-api-integration`
- `short-selling-borrow-cost-and-availability-modeling`
