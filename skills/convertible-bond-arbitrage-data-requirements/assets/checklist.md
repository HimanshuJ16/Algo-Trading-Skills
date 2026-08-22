# Pre-Flight Checklist — Convertible Bond Arbitrage Data

## Static terms and reference data
- [ ] Conversion ratio, coupon rate, coupon frequency and maturity loaded from the terms sheet.
- [ ] Conversion ratio is quoted against the **same nominal** as par value (a ratio "per 100 nominal" against a 1,000 par bond is a silent 10x error).
- [ ] Issuer call, soft-call, put, dividend-protection and takeover-protection features reviewed — none of them are modelled here.

## Market data freshness
- [ ] Stock spot is live; parity is recomputed on every tick used for a hedge decision.
- [ ] CB clean price and accrued interest are current and from a stated source.
- [ ] Stock borrow fee **and** borrow availability refreshed from the stock loan desk before sizing.
- [ ] Expected dividend yield / declared dividends on the underlying loaded.
- [ ] Issuer credit spread (bp) loaded — not defaulted to zero.
- [ ] Delta and implied volatility sourced from a credit-aware CB model or vendor feed, with a timestamp.

## Completeness and validity audit
- [ ] Data audit run and passing: no missing fields, no NaN/infinite/negative values.
- [ ] Delta is within `[0, 1]` and is confirmed to be the **per-share** delta, not shares-per-bond.
- [ ] Metrics are refused, not defaulted, when an input is missing.

## Metrics
- [ ] Parity = conversion ratio x spot.
- [ ] Conversion premium computed, and the price basis (clean vs full) is stated wherever it is reported or compared.
- [ ] Bond floor computed at `risk-free + credit spread`, and recomputed on spread updates.
- [ ] Busted-convert check performed (parity far below the floor => credit trade, not vol arb).

## Hedge sizing
- [ ] Short shares = CB quantity x conversion ratio x per-share delta.
- [ ] Result rounded to the venue lot size, with the rounding residual recorded as known open delta.
- [ ] Delta-drift rebalance band calibrated for this name (cost vs gamma slippage) and wired to a live monitor.

## Carry
- [ ] Coupon income, short-proceeds interest, repo financing, borrow fee and dividends-in-lieu all included.
- [ ] Borrow fee, dividends and proceeds interest applied to the **short position market value** (delta x parity), not to bond notional.
- [ ] Prime-broker haircut on short-proceeds interest reflected.
- [ ] Net carry sign and magnitude sanity-checked against the desk's own funding terms.

## Short leg compliance and operations (US)
- [ ] Locate obtained and order marking handled upstream (Reg SHO 17 CFR §§ 242.200–204).
- [ ] Borrow recall scenario has a defined unwind procedure.

## Screening thresholds
- [ ] Vol-discount, premium-ceiling, carry-floor and busted-ratio thresholds reviewed and calibrated — the shipped defaults are heuristics, not standards.
- [ ] The screen's output is treated as a candidate list, not a trade decision.
