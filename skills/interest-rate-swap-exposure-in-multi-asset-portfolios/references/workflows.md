# Workflows for IRS Exposure Management

## 1. Position ingestion

- Build one `IrsPositionSpec` per live swap.
- Use **remaining** tenor, not original tenor. A 10Y swap with 3 years left carries a ~3Y annuity; using 10 overstates its DV01 by roughly 2.7x.
- `notional_usd` is non-negative — direction is carried by `pay_receive_type`, never by the sign of the notional. A negative notional would flip the sign a second time and is rejected.
- Set `payment_frequency_per_year = 1` for USD SOFR fixed-vs-float (annual both legs, ACT/360). Use `2` only for a legacy semi-annual 30/360 fixed leg.
- `floating_rate_index` must match `currency` (SOFR→USD, ESTR/EURIBOR→EUR, SONIA→GBP, TONA→JPY, SARON→CHF). Mismatches are rejected.

## 2. Annuity and signed DV01

- `swap_annuity_factor(fixed_rate_pct, tenor_years, payment_frequency_per_year)` returns the flat-curve fixed-leg annuity `A = (1/y)·(1 − (1 + y/f)^(−n·f))`, with the removable singularity at `y = 0` replaced by its limit `A = n`. Negative rates are supported while `1 + y/f > 0`.
- `calculate_swap_dv01` returns `+N·A·0.0001` for `PAY_FIXED` and `−N·A·0.0001` for `RECEIVE_FIXED`: signed USD P&L per +1 bps parallel rise.
- Any malformed position raises `ValueError` and aborts the audit. A risk report that silently drops a position it could not parse is worse than no report.

## 3. Aggregation

- `bonds_dv01_usd` must already be signed as P&L per +1 bps rise (negative for a long bond book). Verify this before every run — it is the module's primary misuse risk.
- `equities_notional_usd` is contextual and contributes no DV01 in this model.
- Net DV01 = bond DV01 + Σ swap DV01. The `+10 bps` P&L figure is strictly linear; no convexity.
- Non-USD positions are refused. Convert and aggregate per-curve exposures outside this engine — DV01s on different curves are not additive even after FX conversion.

## 4. Hedge sizing

- Supply `IrsHedgeSpec(tenor_years, fixed_rate_pct, payment_frequency_per_year)` at the **live** par rate for the hedge tenor. The hedge annuity, and therefore the notional, is rate-dependent.
- Omitting it logs a WARNING, falls back to a 5Y at 4.00% placeholder, and sets `hedge_rate_is_default = True`. Treat that output as indicative only.
- Required notional = `−net DV01 / (A_hedge × 0.0001)`.
- Act on `required_hedge_side` + `required_hedge_notional_abs_usd`. The signed `required_hedge_irs_notional_usd` is retained for backward compatibility (negative = receive-fixed). When the side is `NONE` the notional is exactly `0.0` — the two never disagree.

## 5. Audit reporting

- `InterestRateSwapExposureReport` carries the DV01 breakdown, the linear +10 bps P&L, the hedge instruction, and the hedge parameters actually used (`hedge_tenor_years`, `hedge_par_rate_pct`, `hedge_annuity_factor`, `hedge_rate_is_default`) so a reviewer can reproduce the notional.
- `audit_notes` restates the sign convention inline so a downstream reader cannot misinterpret the numbers.

## 6. Before acting on the output

- Confirm `hedge_rate_is_default` is `False`.
- Confirm the bond DV01 sign matches the convention.
- Remember what neutrality does *not* cover: curve twists (no key-rate buckets), convexity beyond ~100 bps, gross notional, and counterparty/CSA exposure.
