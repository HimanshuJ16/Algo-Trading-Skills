---
name: vendor-specific-adjustment-methodology-reconciliation
description: >-
  Use when historical series from different vendors disagree because each applies a
  different corporate action convention, modelling total-return, proportional,
  price-return and raw adjustment and reconciling the cumulative factors.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: data-management-global
  tags: corporate-actions, vendor-reconciliation, crsp, bloomberg, refinitiv, adjustment-factors, stock-splits, cash-dividends
  brokers_frameworks: "crsp; bloomberg-bpipe; refinitiv-elektron; factset; polygon-io"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when ingesting historical price series from multiple market data vendors, building multi-vendor backtesting databases, or auditing corporate action adjustment discrepancies between feeds.

This skill provides institutional mechanisms to:
- Model vendor adjustment conventions as three independent axes — share-count changes, ordinary cash, and abnormal cash — exposed as `CRSP_TOTAL_RETURN`, `BLOOMBERG_PROPORTIONAL`, `SPLIT_ONLY_PRICE_RETURN` and `RAW_UNADJUSTED`.
- Calculate **separate** cumulative price and volume factors: $f_{\text{dist}} = 1 - D / P_{\text{cum}}$ for distributions, $f_{\text{split}} = 1 / S$ for share-count changes, with volume driven by share-count changes alone.
- Detect **cross-vendor price divergences** exceeding a tolerance threshold, including dates that cannot be compared numerically.
- Generate **reconciliation audit reports** carrying divergence counts, maximum and mean percentage variance, and date coverage.

## When NOT to Use

- Do not use this to *discover* a vendor's methodology from prices alone. It emulates a declared convention; it does not infer provenance. Read the vendor's data dictionary and your entitlement configuration.
- Do not treat `CRSP_TOTAL_RETURN` as a description of CRSP's `PRC / CFACPR` field. CRSP sets the Factor to Adjust Price to zero for ordinary cash dividends, so its adjusted price series is a price-return series. See `references/standards.md` §2.
- Do not use this as a point-in-time corporate-action store. It applies the action records you supply; it does not model vendor restatements, late-filed events, or as-of factor revisions.
- Do not use it for delistings, mergers, or total liquidations. CRSP encodes those with a `FACPR` of −1 by convention and they need a terminal-value model, not a multiplicative factor.
- Do not use it to adjust a series a vendor has already adjusted. Adjusting twice is silent and unrecoverable; start from raw exchange prints.

## Prerequisites

- Python 3.10+; standard library only (`datetime`, `dataclasses`, `enum`, `math`, `typing`).
- **Raw, unadjusted** OHLCV bars for one symbol at a time, free of duplicate dates.
- Corporate action records carrying ex-date, action type, and the fields that type requires:
  - splits — `split_ratio` as **new shares per old share** (`2.0` for 2-for-1, `0.1` for 1-for-10 reverse);
  - distributions — `cash_amount` (for a spin-off, the per-share value of the distributed entity) and `cum_price`, the close on the last session *before* the ex-date.
- A decision on whether announced-but-not-yet-effective actions are in your feed. If they are, pass `as_of=` so they cannot adjust history before they have gone ex.

## Workflow

1. **Declare the target methodology first.** Pick the vendor convention you are emulating before touching data; do not let the choice fall out of whichever fields happen to be populated.
2. **Ingest corporate action events.** Construct `CorporateAction` instances. `split_ratio` is new-shares-per-old-share in both directions — a 1-for-10 reverse split is `0.1`, not `10.0`. Getting this backwards inverts the entire history silently in a naive implementation; here it produces a mathematically valid but wrong series, so verify the convention against the vendor record.
3. **Calculate cumulative adjustment factors.** `calculate_adjustment_factors(bars, actions, methodology, as_of=None)` returns `{date: (price_factor, volume_factor)}`. The two factors differ whenever a cash distribution or spin-off is present; that is the point, not a bug.
4. **Adjust the raw series.** `adjust_price_series(bars, actions, methodology, as_of=None, price_decimals=None)`. Rounding is opt-in and off by default — round for display, never before storing a series that feeds further arithmetic.
5. **Handle the validation errors rather than suppressing them.** `AdjustmentValidationError` on a distribution greater than or equal to `cum_price`, a non-positive `split_ratio`, a missing `cum_price`, a foreign-symbol action, or duplicate/non-finite bars means the corporate-action record is wrong. Fix the record; do not widen the guard.
6. **Reconcile cross-vendor series.** `reconcile_vendor_series(symbol, series_a, vendor_a_name, series_b, vendor_b_name, tolerance_pct=0.5, min_coverage_pct=0.0)`.
7. **Read the report on both dimensions.** `status == "PASSED"` is only meaningful alongside `coverage_pct`; a clean result over a third of the dates is not a clean series. Set `min_coverage_pct` when the overlap itself is part of what you are auditing.
8. **Classify each divergence before acting.** `reason == "NON_FINITE_PRICE"` or `"NON_POSITIVE_MID_PRICE"` is a data-corruption finding, not a methodology finding. A cluster of divergences that starts on a single date and persists backwards is an unreconciled corporate action; scattered sub-1% differences are usually rounding or a different cum-price convention.

## Common Pitfalls

- **Scaling volume with the full price factor.** Volume moves only for actions that change shares outstanding. Deriving the volume factor as $1/F^{\text{price}}$ inflates pre-dividend volume by $1/(1-D/P)$ — about 5% for a 5% yield event — and quietly corrupts ADV, turnover and participation-rate limits that gate live order sizing.
- **Assuming ex-dates land on trading days.** Match actions to bars by `ex_date > bar_date`, not by dictionary lookup on the ex-date. An ex-date on a market holiday or inside a data gap will otherwise drop the adjustment entirely and leave a raw split jump in the middle of the series.
- **One action per ex-date.** A split and a dividend can share an ex-date, and so can two dividends. A `{date: factor}` map silently keeps the last one. Multiply distinct actions; sum same-date ordinary cash dividends before computing the factor.
- **Mixing total return and price return feeds.** A total-return series and a price-return series for the same symbol diverge on every date before a dividend ex-date. Merging them injects false price jumps into factor signals; reconcile before merging, never after.
- **Spin-off treatment.** A spin-off adjusts price but not volume: it changes company assets, not shares outstanding. Vendors also disagree on whether to model it as a value distribution or to issue a synthetic child instrument, so an unreconciled spin-off is a common source of large PnL jumps.
- **Cum-date price mis-alignment.** The proportional factor denominator is the close on the last session *before* the ex-date. Using the ex-date close instead builds the price drop into the denominator and understates the factor.
- **Reading `PASSED` from a `nan`.** `nan > tolerance` is `False`, so any naive tolerance check reports agreement on corrupt data. Non-finite closes must be flagged explicitly.
- **Rounding every adjusted bar.** Rounding to four decimals on write accumulates tracking error over long histories and, at sub-dollar prices, can itself exceed a 0.5% reconciliation tolerance.
- **Applying an announced action before its ex-date.** An adjustment applied ahead of its ex-date is look-ahead bias in the price series itself. Use `as_of=` when the action feed includes announced events.

## Verification

Run the unit test suite. It covers forward and reverse split factors, dividend price adjustment, volume invariance under cash distributions and spin-offs, same-ex-date aggregation, ex-dates without a matching bar, `as_of` suppression, methodology axis selection, input validation, and reconciliation coverage and non-finite handling:

```bash
python -m unittest discover -s skills/vendor-specific-adjustment-methodology-reconciliation/scripts
```

Production sign-off additionally requires replaying the vendor's own published factors against a symbol with a known split, a known dividend, and a known spin-off, and confirming that adjusted dollar volume ($P_{\text{adj}} \times V_{\text{adj}}$) is preserved across split events and unchanged across dividend events.

## Related Skills

- `adjusted-vs-unadjusted-price-series-pitfalls`
- `corporate-action-adjusted-backtesting`
- `corporate-action-event-calendar-integration`
- `data-vendor-cross-validation-for-backtests`
- `multi-source-price-reconciliation-tie-breaking`
- `vendor-outage-fallback-data-source-hierarchy`
