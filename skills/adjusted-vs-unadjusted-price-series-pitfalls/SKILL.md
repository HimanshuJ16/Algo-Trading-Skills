---
name: adjusted-vs-unadjusted-price-series-pitfalls
description: Audit historical OHLCV series and corporate-action metadata for split, dividend, adjustment-mode, and continuity errors before backtesting.
  Separates raw price-return, split-adjusted, and total-return semantics; it does not infer point-in-time vendor provenance from prices alone.
domain: algorithmic-trading
subdomain: backtesting-methodology
tags:
- backtesting-methodology
- price-adjustment
- stock-splits
- dividends
- data-integrity
- look-ahead-bias
brokers_frameworks:
- Price Adjustment Auditor
- Python
version: "1.3.0"
author: System
license: MIT
---

## When to Use

Invoke this skill before loading historical OHLCV data into a backtest when corporate actions, vendor adjustment factors, or mixed price-series conventions may affect signals, returns, liquidity, or universe comparisons.

Declare the intended `SeriesAdjustmentMode` explicitly:

- `UNADJUSTED`: historical prices retain split and cash-dividend ex-date moves; model splits and dividends separately.
- `SPLIT_ADJUSTED`: split history is normalized, while cash dividends remain explicit events unless the vendor contract says otherwise.
- `TOTAL_RETURN_ADJUSTED`: split and dividend effects are embedded for return analysis; validate the factor methodology and point-in-time availability.
- `UNKNOWN`: audit continuity and actions, but do not infer provenance merely because no discontinuity is found.

## When NOT to Use

- Do not infer vendor adjustment provenance from a smooth series alone; continuity is not evidence of correct adjustment factors.
- Do not treat every ex-dividend price drop as look-ahead bias. A raw price series can legitimately drop by the cash dividend amount; the portfolio must separately receive the dividend.
- Do not use a price-only audit to satisfy point-in-time or vendor-revision requirements. Those require as-of corporate-action snapshots and adjustment-factor history.
- Do not apply split adjustment to data already adjusted by the vendor without recording the factor source and convention.
- Do not use adjusted close as a substitute for executable OHLC, intraday prices, quotes, or volume without validating the vendor's field definitions.

## Prerequisites

- Historical dates, closes, volumes, and preferably actual next-session opens.
- Corporate-action records with ISO dates, action type, and ratio convention: `SPLIT` ratio is post-split shares per pre-split share; `DIVIDEND` ratio is cash per share.
- A declared `SeriesAdjustmentMode` and documented vendor/factor provenance.
- A point-in-time policy for when corporate actions and adjustment factors become available to the backtest.
- A tolerance policy for price, volume, and notional reconciliation.

## Workflow

1. **Declare semantics**: Select `UNADJUSTED`, `SPLIT_ADJUSTED`, `TOTAL_RETURN_ADJUSTED`, or `UNKNOWN` before auditing. Do not let the auditor guess the series mode.
2. **Validate inputs**: Confirm strictly increasing ISO dates, aligned lengths, finite positive prices, non-negative volumes, and valid corporate-action records.
3. **Scan the correct boundary**: Provide `opens` so discontinuities are measured from prior close to next open. If opens are unavailable, the auditor falls back to the next close, logs a warning, and records `boundary_source="PRIOR_CLOSE_FALLBACK"` on the report. Treat any audit carrying that value as provisional.
4. **Match actions**: The auditor builds one composite expected price ratio per ex-date — split factors (`1 / ratio`) multiplied together and multiplied by the cash factor `(prev_close - total_dividend) / prev_close` — and compares it with the observed ratio using `price_match_tolerance_pct` (default 5%). Volume scaling is compared separately against the split ratio using `volume_ratio_tolerance_pct` (default 25%). Keep these two tolerances distinct: the ex-date price factor is mechanical, traded volume is not.
5. **Interpret the report**:
   - `is_consistent` means no detected discontinuity, not that adjustment provenance is proven.
   - `unexplained_discontinuities` identifies jumps not explained by the declared mode and known actions.
   - `expected_price_ratio` on each event exposes the composite factor the jump was tested against; `None` means no expectation could be formed.
   - `detected_adjustment_type` reports only what the jumps prove: a matched split jump gives `UNADJUSTED`, a matched cash-dividend jump only gives `NOT_TOTAL_RETURN_ADJUSTED` (raw and split-adjusted remain indistinguishable), and no matched evidence gives `UNKNOWN`.
   - `has_look_ahead_bias_risk` is raised for dividend discontinuities that conflict with `TOTAL_RETURN_ADJUSTED`; point-in-time availability still requires an external audit.
6. **Transform only with provenance**: Use `apply_split_adjustment` for a documented split ratio and index convention. It adjusts prices before the split by dividing by the ratio and volumes by multiplying by the ratio, without lossy rounding.
7. **Validate the universe**: Run `validate_universe_consistency` and reject mixed declared series modes or incompatible detected types before calculating cross-asset signals.
8. **Persist evidence**: Store raw data identifiers, action records, series mode, factor source/version, as-of timestamp, tolerance settings, audit report, and transformation parameters.

## Common Pitfalls

- **Close-to-close substitution**: Using a close value while labeling it `next_open` can miss overnight gaps and misclassify actions.
- **Dividend semantic collapse**: Cash dividends, split-adjusted prices, and total-return prices answer different research questions.
- **Ratio convention mismatch**: A `2.0` split means two post-split shares per old share; a `0.5` reverse split doubles historical prices under backward adjustment.
- **Lossy rounding**: Rounding every adjusted bar to four decimals can accumulate tracking error in long histories and volume-weighted calculations.
- **Multiple same-day actions**: A split and dividend can share an ex-date. Testing the jump against either action alone is wrong — the factors multiply, so a 2-for-1 split plus a $10 dividend on a $100 close expects `0.5 * 0.9 = 0.45`, not `0.5`. Same-date cash dividends are summed before the cash factor is formed.
- **Tolerance conflation**: A loose volume tolerance applied to the price ratio silently explains away real data errors. At a 25% price tolerance a 42% overnight crash "matches" a 2-for-1 split; keep the price tolerance tight and reconcile against vendor factors rather than widening it.
- **False provenance**: No detected jump does not prove a series is adjusted, correctly adjusted, or point-in-time safe.

## Verification

Run the focused tests:

```text
python -m unittest discover -s skills/adjusted-vs-unadjusted-price-series-pitfalls/scripts
```

The tests cover split and dividend semantics, close/open detection and boundary provenance, composite same-ex-date factors, price-match tolerance behavior, total-return risk, no-jump ambiguity, provenance inference limits, forward and reverse splits, precision, ISO date canonicalization, invalid inputs, and universe-mode consistency. Production sign-off additionally requires replaying vendor factors and comparing raw versus transformed price, volume, dividend, and total-return ledgers.

## Related Skills

- `backtest-determinism-and-reproducibility`
- `corporate-action-adjusted-backtesting`