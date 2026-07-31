---
name: lse-millennium-exchange-api
description: >-
  Quantitative market gateway engine for the London Stock Exchange (LSE Millennium Exchange platform), enforcing TIDM ticker codes, GBX (Pence) currency notation, and MiFID II RTS 28 tick size schedules.
domain: Global Market Integration & FX
subdomain: European Equities & LSE Connectivity
tags: ["lse", "london-stock-exchange", "millennium-exchange", "gbx", "pence-quoting", "tidm", "mifid-ii", "sets"]
brokers_frameworks: ["LSE Millennium FIX Protocol", "LSE OUG Native Binary", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when developing order gateway interfaces and execution algorithms for the London Stock Exchange (LSE Millennium Exchange platform). LSE equities (e.g. `SHEL` Shell, `AZN` AstraZeneca, `HSBA` HSBC) are identified by **TIDM (Tradable Instrument Display Mnemonic)** tickers and are quoted/traded in **GBX (Pence)**, NOT GBP ($\pounds 1.00 = 100\text{ GBX}$). Submitting orders in GBP instead of GBX results in severe $100\times$ order sizing mispricing.

## Prerequisites

- LSE order payload (`tidm`: `'SHEL'`, `side`: `'BUY'`/`'SELL'`, `price_gbx`, `quantity_shares`).
- LSE MiFID II RTS 28 dynamic tick size schedule.

## Workflow

1. **TIDM Ticker & Currency Quoting Audit**:
   - Verify TIDM ticker format (2-4 uppercase letters, e.g. `SHEL`, `AZN`, `BARC`).
   - Enforce **GBX (Pence)** price notation.
2. **LSE Dynamic Tick Size Calculation**:
   - Compute dynamic tick size $\Delta P$ (in GBX):
     - $P < 10.0\text{ GBX} \implies \Delta P = 0.01\text{ GBX}$.
     - $10.0 \le P < 50.0\text{ GBX} \implies \Delta P = 0.05\text{ GBX}$.
     - $50.0 \le P < 100.0\text{ GBX} \implies \Delta P = 0.10\text{ GBX}$.
     - $100.0 \le P < 500.0\text{ GBX} \implies \Delta P = 0.20\text{ GBX}$.
     - $500.0 \le P < 1,000.0\text{ GBX} \implies \Delta P = 0.50\text{ GBX}$.
     - $1,000.0 \le P < 5,000.0\text{ GBX} \implies \Delta P = 1.00\text{ GBX}$.
     - $P \ge 5,000.0\text{ GBX} \implies \Delta P = 2.00\text{ GBX}$.
3. **Price Tick & GBP Notional Calculation**:
   - Verify order price is an exact integer multiple of $\Delta P$.
   - Calculate equivalent GBP notional value: $V_{\text{GBP}} = \frac{P_{\text{GBX}} \times \text{quantity}}{100.0}$.
4. **Audit Report Generation**: Output structured `LseOrderReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Submitting Prices in GBP Instead of GBX**: Submitting $26.50$ instead of $2650.0\text{ GBX}$ for Shell, causing order rejection or massive $100\times$ under-pricing.
- **Hardcoding 1 Pence Tick Sizes**: Assuming all LSE stocks trade in $1\text{ GBX}$ increments, ignoring MiFID II sub-penny and multi-pence tick tiers.
- **Confusing TIDM with US Ticker Symbols**: Passing RIC codes (`SHEL.L`) into Millennium FIX fields expecting clean TIDM (`SHEL`).

## Verification

- Instantiate `LseMillenniumExchangeApiEngine`. Route Shell order (`tidm="SHEL"`, Price $= 2650.0\text{ GBX}$, Qty $= 1,000$ shares) $\implies$ verify tick size $\Delta P = 1.00\text{ GBX}$, total notional $= \pounds 26,500.00$ GBP, and approves `LSE_ORDER_VALIDATED`. Audit Invalid Tick ($2650.35\text{ GBX}$) $\implies$ verify `INVALID_TICK_SIZE`.
- Run `python scripts/test_lse_millennium_exchange_api.py`.

## Related Skills

- `currency-pair-quoting-convention-normalization`
- `exchange-tick-size-regime-tracking`
---
