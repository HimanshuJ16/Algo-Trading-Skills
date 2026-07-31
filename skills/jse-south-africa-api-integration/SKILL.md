---
name: jse-south-africa-api-integration
description: >-
  Quantitative market gateway engine for Johannesburg Stock Exchange (JSE South Africa Millennium Exchange), enforcing 3-letter alpha tickers, ZAC South African Cents pricing, and JSE tick size tiers.
domain: Global Market Integration & FX
subdomain: African Market Connectivity & JSE Gateway
tags: ["jse", "south-africa", "johannesburg-stock-exchange", "zac-cents", "millennium-exchange", "naspers", "tick-sizes"]
brokers_frameworks: ["JSE Millennium Exchange FIX API", "JSE Nutron / YieldX Gateway", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when connecting execution algorithms to the Johannesburg Stock Exchange (JSE / South Africa Millennium Exchange). Order submission to the JSE requires strict adherence to African market conventions: 3-letter alpha ticker codes (e.g. `NPN` Naspers, `PRX` Prosus, `AGL` Anglo American), **ZAC South African Cents** currency notation ($1\text{ ZAR} = 100\text{ ZAC}$), and JSE Cent tick size tiers.

## Prerequisites

- JSE order payload (`alpha_code`: `NPN`, `side`: `BUY`/`SELL`, `price_zac` in Cents, `quantity_shares`, `reference_price_zac`).
- Official JSE Millennium Exchange tick size schedule.

## Workflow

1. **3-Letter Alpha Ticker Validation**:
   - Verify ticker is 3 uppercase letters (`NPN`, `PRX`, `AGL`).
2. **ZAC Cents Currency & Tick Size Audit**:
   - Verify order price is in **ZAC Cents** (e.g. ZAR 300.00 $\to$ 30,000 ZAC).
   - Compute dynamic tick size $\Delta P$:
     - $P < 10,000\text{ ZAC} \implies \Delta P = 1\text{ ZAC}$.
     - $P \ge 10,000\text{ ZAC} \implies \Delta P = 5\text{ ZAC}$.
   - Verify order price is an exact integer multiple of $\Delta P$.
3. **ZAR Notional Value Conversion**:
   - Calculate equivalent ZAR notional: $\text{Notional}_{\text{ZAR}} = \frac{\text{price\_zac} \times \text{quantity}}{100.0}$.
4. **Daily Price Bound Audit**:
   - Verify price is within $\pm 15\%$ of reference price.
5. **Audit Report Generation**: Output structured `JseOrderReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Submitting Prices in ZAR Instead of ZAC**: Submitting ZAR 300 instead of 30,000 ZAC, resulting in a 100x pricing error and immediate order rejection or massive execution loss.
- **Using 4-Letter Tickers**: Submitting 4-letter or US ADR tickers (`NPSNY`) to the JSE instead of 3-letter local alpha codes (`NPN`).
- **Violating 5 ZAC Tick Increments**: Submitting 30,002 ZAC for a stock priced above 10,000 ZAC (where tick size is 5 ZAC).

## Verification

- Instantiate `JseSouthAfricaApiEngine`. Route Naspers order (`alpha_code="NPN"`, Price $= 85,500\text{ ZAC}$ / ZAR 855.00, Qty $= 100$ shares, Ref Price $= 85,500\text{ ZAC}$) $\implies$ verify tick size $\Delta P = 5\text{ ZAC}$, calculates Notional ZAR $=\text{ZAR } 85,500.00$, and approves `JSE_ORDER_VALIDATED`.
- Run `python scripts/test_jse_south_africa_api_integration.py`.

## Related Skills

- `currency-pair-quoting-convention-normalization`
- `exchange-tick-size-regime-tracking`
---
