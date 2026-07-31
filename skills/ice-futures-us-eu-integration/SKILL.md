---
name: ice-futures-us-eu-integration
description: >-
  Quantitative market connectivity engine for Intercontinental Exchange (ICE Futures US & Europe), enforcing FIX Tag 207 MIC routing, contract month symbol formatting, and NCR reasonability price limits.
domain: Global Market Integration & FX
subdomain: Commodity & Energy Derivatives Gateway
tags: ["ice-futures", "brent-crude", "sugar-no-11", "fix-protocol", "ifeu", "ifus", "ncr-price-banding"]
brokers_frameworks: ["ICE iMpact Multicast", "ICE FIX API", "FIX 4.2 / 4.4", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in global futures execution algorithms, energy trading strategies, and multi-asset derivative gateways routing to Intercontinental Exchange (ICE Futures Europe `IFEU` and ICE Futures US `IFUS`). Trading ICE contracts (Brent Crude `B`, Dutch TTF Gas `T`, Sugar No. 11 `SB`, US Dollar Index `DX`) requires strict adherence to ICE contract month formatting (`<ROOT><MONTH><YY>`), FIX Tag 207 MIC venue routing (`IFEU` vs `IFUS`), contract multiplier valuation, and **ICE No-Cancellation Range (NCR) reasonability price limits**.

## Prerequisites

- ICE order request (`root_symbol`, `month_code`, `year`, `side`, `price`, `quantity`, `bbo_price`).
- ICE Contract Catalog (`multiplier`, `tick_size`, `exchange_mic`: `IFEU` / `IFUS`, `max_ncr_ticks`).

## Workflow

1. **ICE Contract Month Symbol Formatting**:
   - Map month code (F, G, H, J, K, M, N, Q, U, V, X, Z) and year to ICE symbol (e.g. `B` + `Z` + `26` $\implies$ `"BZ26"` for Brent Crude Dec 2026).
2. **FIX 4.2 / 4.4 Tag Formatting**:
   - Format `Tag 55` (Symbol), `Tag 207` (SecurityExchange MIC `IFEU`/`IFUS`), `Tag 200` (MaturityMonthYear `YYYYMM`).
3. **Valuation & Price Tick Alignment**:
   - Compute Notional Value: $\text{Notional} = \text{Price} \times \text{Multiplier} \times \text{Quantity}$.
   - Compute Tick Value: $\text{Tick Value} = \text{Tick Size} \times \text{Multiplier}$.
   - Verify price is a valid integer multiple of `tick_size`.
4. **ICE No-Cancellation Range (NCR) Price Audit**:
   - Audit price deviation from current BBO: $|\frac{P_{\text{order}} - P_{\text{bbo}}}{\text{Tick Size}}| \le \text{max\_ncr\_ticks}$.
5. **Audit Report Generation**: Output structured `IceFuturesOrderReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Incorrect FIX SecurityExchange MIC Routing**: Setting `Tag 207 = IFUS` for Brent Crude (traded on `IFEU`), causing order gateway rejection.
- **Ignoring Contract Multipliers in Risk Limits**: Evaluating position risk by raw contract quantity instead of multiplied Notional Value (e.g. 1 contract of Brent Crude $= 1,000\text{ barrels} \approx \$75,000$).
- **Breaching ICE Reasonability Limits (NCR)**: Routing aggressive limit orders beyond ICE NCR limits, triggering exchange order rejection and trading warnings.

## Verification

- Instantiate `IceFuturesIntegrationEngine`. Route Brent Crude Dec 2026 order (`root="B"`, `month="Z"`, `year=2026`, Price $=\$75.50$, Qty $=10$, BBO $=\$75.40$). Verify engine formats contract `"BZ26"`, assigns `Tag 207 = IFEU`, calculates Notional $=\$755,000$ ($10 \times 75.50 \times 1000$), confirms tick alignment ($\$0.01$), and passes NCR price audit ($10\text{ ticks} \le 100$).
- Run `python scripts/test_ice_futures_us_eu_integration.py`.

## Related Skills

- `futures-contract-roll-automation`
- `exchange-tick-size-regime-tracking`
---
