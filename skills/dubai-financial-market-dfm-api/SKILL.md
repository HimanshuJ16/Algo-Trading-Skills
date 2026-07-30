---
name: dubai-financial-market-dfm-api
description: >-
  Quantitative venue integration engine for Dubai Financial Market (DFM) FIX 4.4 protocol, validating National Investor Numbers (NIN), AED tick size regimes, and 10% daily price band circuit breakers.
domain: Venue Integration & Protocols
subdomain: Middle East (GCC/MENA) Exchange Integration
tags: ["dfm", "dubai-financial-market", "gcc-markets", "fix-4.4", "nin-investor-number", "aed-currency", "mena-trading"]
brokers_frameworks: ["DFM FIX 4.4 Gateway", "Dubai CSD", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in Middle Eastern (GCC/MENA) quantitative trading systems, broker execution engines, and regional market making algorithms connecting to the Dubai Financial Market (DFM) and Nasdaq Dubai. DFM operates under UAE SCA regulations. Trading requires attaching a 10-digit National Investor Number (NIN) to order messages, quoting prices in United Arab Emirates Dirham (AED), and adhering to DFM price-band tick size rules and $\pm 10\%$ daily circuit breaker limits.

## Prerequisites

- DFM FIX 4.4 session credentials (`SenderCompID`, `TargetCompID`, `BrokerAccount`).
- National Investor Number (NIN) (e.g. `1099887766`).
- Instrument ticker symbol (e.g. `EMAAR` - Emaar Properties, `DEWA` - Dubai Electricity and Water).

## Workflow

1. **Investor NIN & Account Tag Validation**:
   - Verify presence of valid 10-digit National Investor Number (NIN) in FIX Tag 1 (`Account`).
2. **AED Tick Size Regime Audit**:
   - Audit order price against DFM 2026 tick rules:
     - $P < 1.00 \text{ AED} \implies \text{Tick} = 0.001 \text{ AED}$.
     - $1.00 \le P < 10.00 \text{ AED} \implies \text{Tick} = 0.01 \text{ AED}$.
     - $10.00 \le P < 50.00 \text{ AED} \implies \text{Tick} = 0.02 \text{ AED}$.
     - $P \ge 50.00 \text{ AED} \implies \text{Tick} = 0.05 \text{ AED}$.
3. **Daily Circuit Breaker Band Check**:
   - If order price $P$ deviates $> 10\%$ from prior day settlement price $\implies$ Reject order (`CIRCUIT_BREAKER_BAND_BREACH`).
4. **FIX 4.4 Message Construction & Dispatch**: Output structured `DfmOrderExecutionReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Omitting National Investor Number (NIN)**: Sending FIX order messages without a valid NIN in Tag 1, triggering immediate exchange gateway rejection.
- **Off-Tick Pricing in AED**: Submitting order price 7.855 AED on Emaar Properties, violating the 0.01 AED tick step for prices between 1.00 and 10.00 AED.
- **Trading Outside DFM Session Hours**: Attempting order entry outside 10:00 - 14:45 GST continuous trading hours.

## Verification

- Instantiate `DubaiFinancialMarketApiEngine`. Construct order for `EMAAR` (NIN = `1099887766`, Price = 7.85 AED, Qty = 10,000, Prior Settlement = 7.80 AED). Verify engine validates NIN, passes 0.01 AED tick step, passes 10% circuit breaker check, and generates FIX 4.4 message. Construct order with off-tick price (7.855 AED). Verify engine flags `INVALID_TICK_SIZE` rejection.
- Run `python scripts/test_dubai_financial_market_dfm_api.py`.

## Related Skills

- `exchange-tick-size-regime-tracking`
- `currency-pair-quoting-convention-normalization`
---
