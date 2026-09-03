---
name: interest-rate-swap-exposure-in-multi-asset-portfolios
description: >-
  First-order interest-rate risk for vanilla fixed-vs-float swaps held beside bonds and equities: annuity-based swap DV01/PV01, signed Pay-Fixed vs Receive-Fixed exposure, USD-only aggregation, and the par-swap notional that flattens net portfolio DV01.
domain: Portfolio Multi-Strategy
subdomain: Fixed Income Risk & IRS Exposure Management
tags: ["interest-rate-swap", "irs", "dv01", "pv01", "annuity-factor", "sofr", "multi-asset-risk", "duration-hedging"]
brokers_frameworks: ["SOFR fixed-vs-float swap conventions", "PV01 annuity framework", "Python Dataclasses"]
version: "1.1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when a multi-asset portfolio holds vanilla fixed-vs-float Interest Rate Swaps alongside bonds and equities, and you need a *first-order* view of parallel-shift rate risk: how much the book makes or loses per basis point, and what swap notional flattens it.

An IRS exchanges fixed-rate cash flows for a floating index (SOFR, ESTR, SONIA). Because DV01 is additive **within a single curve**, this module computes signed swap DV01 from the fixed-leg annuity, aggregates it with bond DV01, and sizes the par swap required for DV01 neutrality.

## When NOT to Use

This is a flat-curve, closed-form estimator, not a pricing library. Do not use it for booking, margin, CVA, key-rate/bucketed risk, curve trades (steepeners, butterflies), forward-starting or amortising swaps, swaptions or other optionality, or any shock large enough for convexity to matter (materially beyond ~100 bps). Those need a bootstrapped discount curve and a full cash-flow engine.

## Prerequisites

- Python 3.9+. No third-party dependencies.
- IRS position payload: `swap_id`, `notional_usd` (non-negative), `pay_receive_type` (`PAY_FIXED` / `RECEIVE_FIXED`), `fixed_rate_pct` (percent), `tenor_years` (**remaining** tenor), `floating_rate_index`, `payment_frequency_per_year` (1 for USD SOFR), `currency`.
- Bond portfolio DV01 **already signed as P&L per +1 bps rise** (negative for a long bond book) — see the sign convention below.
- Current par swap rate and tenor for the hedge instrument, supplied via `IrsHedgeSpec`.

## Sign Convention

Every DV01 here is **signed USD P&L for a +1 bps parallel rise** — the *negative* of the textbook `DV01 = -dV/dy`, under which a long bond has a positive DV01.

| Position | `dv01` in this module |
|---|---|
| Long bond book | **Negative** (loses when rates rise) |
| Pay-fixed swap (short duration) | **Positive** |
| Receive-fixed swap (long duration) | **Negative** |

Supplying `bonds_dv01_usd` in the opposite convention makes the engine size a hedge that **doubles** rate exposure instead of neutralising it. Check the sign before every run.

## Workflow

1. **Ingest positions**: build `IrsPositionSpec` per swap. Use *remaining* tenor, not original tenor — a 10Y swap with 3 years left has roughly a 3Y annuity. Set `payment_frequency_per_year=1` for USD SOFR fixed-vs-float (annual on both legs); use `2` only for a legacy semi-annual 30/360 fixed leg.
2. **Compute the annuity, not "tenor / 2"**: `swap_annuity_factor` returns `A = (1/y)(1 - (1 + y/f)^(-n·f))`, the flat-curve fixed-leg annuity `Σ δ_i·DF_i`. For a 5Y annual swap at 4.25%, `A = 4.4207`, so a $10M swap is $4,420.73/bps — **not** the $2,500 a `tenor/2` duration would imply.
3. **Sign the DV01**: `calculate_swap_dv01` returns `+N·A·0.0001` for `PAY_FIXED` and `-N·A·0.0001` for `RECEIVE_FIXED`. It raises `ValueError` on an unknown side, unknown index, index/currency mismatch, non-USD currency, negative or non-finite notional, or non-positive tenor — a malformed position fails the audit rather than being silently priced or dropped.
4. **Aggregate and size the hedge**: `analyze_portfolio_irs_exposure` sums bond and swap DV01 and divides the residual by the hedge swap's own DV01-per-dollar. Supply `IrsHedgeSpec(tenor_years, fixed_rate_pct)` at the *live* par rate — the hedge annuity, and therefore the notional, depends on it. If you omit it the engine logs a WARNING, falls back to a 5Y at 4.00% placeholder, and sets `hedge_rate_is_default=True`; treat that output as indicative only.
5. **Read the side, not the sign**: act on `required_hedge_side` (`PAY_FIXED` / `RECEIVE_FIXED` / `NONE`) plus `required_hedge_notional_abs_usd`. `required_hedge_irs_notional_usd` is signed (negative = receive-fixed) and is retained for backward compatibility. When the side is `NONE` the notional is exactly `0.0`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Approximating swap duration as `tenor / 2`**: the correct multiplier is the fixed-leg annuity. At a 5Y tenor `tenor/2 = 2.5` against an annuity of 4.42 — the swap DV01 is understated by ~43%, so a book hedged on that basis is left materially long duration while the report claims neutrality.
- **Passing bond DV01 in the textbook sign convention**: a long bond book must be supplied as a *negative* number here. Get it backwards and the engine doubles the exposure it was asked to hedge.
- **Using original tenor instead of remaining tenor**: a swap two years from maturity does not carry its original 10Y annuity. Feeding original tenor systematically overstates portfolio DV01 and oversizes the offsetting hedge.
- **Sizing the hedge off a stale or placeholder par rate**: the hedge annuity is rate-dependent, so a wrong rate produces a wrong notional. Check `hedge_rate_is_default` before executing anything.
- **Aggregating across curves**: USD SOFR DV01 and EUR ESTR DV01 are not additive even after FX conversion — they are sensitivities to *different* curves. This engine refuses non-USD positions; convert and aggregate per-curve exposures outside it.
- **Treating DV01 neutrality as risk neutrality**: net-zero DV01 says nothing about curve twists (no key-rate buckets), convexity on large moves, counterparty/CSA exposure, or the gross notional still outstanding — two offsetting swaps net to zero DV01 while leaving $20M of gross notional and full counterparty risk.

## Verification

- 5Y annual pay-fixed, $10M at 4.25%: annuity `4.4207289459`, DV01 `= +$4,420.73/bps`. Receive-fixed is the exact negative.
- Bond-only book at `bonds_dv01_usd = -$5,000/bps`, hedged with a 5Y par swap at 4.00% (annuity `4.4518223310`): `required_hedge_side = PAY_FIXED`, notional `= 5,000 / (4.4518223310 × 10⁻⁴) = $11,231,355.67`. Booking that hedge and re-running returns net DV01 `0.00` and side `NONE`.
- The test suite derives every expected annuity from an explicit period-by-period cash-flow summation, independent of the closed form under test.

```bash
python -m unittest discover -s skills/interest-rate-swap-exposure-in-multi-asset-portfolios/scripts
```

## Related Skills

- `total-return-swap-synthetic-exposure`
- `counterparty-credit-risk-for-otc-derivatives`
- `multi-currency-var-aggregation`
- `portfolio-stress-test-including-liquidity-crunch-scenarios`
