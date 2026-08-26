# Deep Workflow Reference — multi-source-price-reconciliation-tie-breaking

This file holds the full technical procedure referenced by `SKILL.md`.

## 0. Preconditions

- **Three or more genuinely independent vendors.** Two aggregators reselling the same
  underlying feed are one source wearing two badges: they will agree with each other
  while being wrong together, and the median will follow them.
- **One price basis, one currency.** Last trade against quote midpoint diverges by
  roughly half a spread permanently, so the engine will report a standing divergence
  that never resolves. Normalise upstream.
- **One local receipt clock.** `timestamp` and `as_of` are receipt times, never
  vendor- or exchange-supplied event times.
- **Strictly positive prices.** Percentage-of-median arithmetic is undefined at or
  below zero. Instruments that can settle negative need an absolute-difference
  reconciliation instead.

## 1. Batch validation, before any arithmetic

| Condition | Action | Why |
|---|---|---|
| Empty batch | raise | Nothing to reconcile. |
| Non-finite price | raise at quote construction | `abs(nan − m)/m > bound` is `False`, so a `NaN` is classified *valid*, enters the composite, and is published. It then compares `False` against every downstream limit too. |
| Price `<= 0` | raise at quote construction | A non-positive median disables the deviation test and forces the observed spread to zero, so garbage reads as unanimous agreement. |
| `reliability_weight <= 0` | raise at quote construction | Zero weights collapse the composite denominator (`ZeroDivisionError`); negative weights place the composite outside the quote range. |
| `symbol` mismatch | raise | A dispatcher bug that produces a confident price for the wrong security. |
| Duplicate `vendor_id` | raise | Biases the median toward the duplicated vendor, double-weights the composite, and turns "3 sources agreed" into "2 sources, one counted twice". |

## 2. Staleness gate, before the median

Staleness is applied **first**, not as a tie-breaker, because a stale quote inside the
deviation bound is structurally invisible to outlier logic. A vendor frozen 100 seconds
ago at 0.79% from the median survives a 1% deviation test, widens the observed spread
past a 5 bps tolerance, and converts two fresh vendors agreeing to 2 bps into an
unresolved divergence.

| Condition | Result |
|---|---|
| `max_quote_age_seconds is None` | Gating disabled; every quote is usable. `as_of` is inert and logs a warning. |
| Gating enabled, `as_of` omitted | raise |
| `as_of − timestamp > max_quote_age_seconds` | Quote moves to `stale_quotes`, excluded from all arithmetic |
| `as_of − timestamp < 0` | Logged as a receipt-clock warning; the quote is still used |
| Every quote stale | `RECONCILIATION_NO_USABLE_QUOTE`, `canonical_price is None` |

The comparison is strict `>`: a quote exactly at the age limit is still fresh.

`as_of` is a required argument, never defaulted to `max(timestamp)`. That default makes
the newest quote zero seconds old by construction, so the batch in which every vendor
has stopped updating — the outage that matters most — always looks fresh.

## 3. Outlier attribution

```
n = len(usable)
if n < min_sources_for_outlier_filter:   # default 3
    skip filtering; record that it was skipped
else:
    m_all = median(usable prices)
    bound = max(max_deviation_pct, min_absolute_tolerance / m_all)
    reject q where |q.price − m_all| / m_all > bound
```

**Why three.** For two quotes `a < b`, the median is `(a+b)/2`, so
`|a − m| = |b − m| = (b−a)/2`. Any symmetric distance test passes both or fails both,
and no outlier is ever attributable. The engine skips the filter and says so, rather
than running it and reporting "0 outliers" — which on a dashboard reads as *checked
and clean* rather than *not checkable*.

**Why not MAD.** A median-absolute-deviation / modified-z-score filter adapts its
threshold to the observed dispersion. Vendor quotes for a liquid instrument cluster
tightly, so MAD collapses toward zero and an ordinary one-tick disagreement scores as
an extreme outlier; when more than half the quotes are identical — routine on a
discrete tick grid — MAD is exactly zero and the score is undefined. A fixed economic
bound degrades gracefully in both cases and can be calibrated per instrument from
recorded data.

**The deadlock.** With an even count split into two clusters (100, 100, 105, 105) the
median falls in the gap and *every* quote fails the bound. That is a deadlock, not a
detection. The engine retains all quotes, sets `filter_deadlocked=True`, forces
`is_cross_verified=False`, and resolves by tie-breaker. What it must never do — and
what version 1.x did — is reset the rejection list to empty and report
`RECONCILIATION_SUCCESS` with `valid=4, outliers=0`. That audit record states the
opposite of what happened.

For an odd count the median is itself one of the quotes, so at least one always
survives with distance exactly zero.

## 4. Agreement audit

```
m_valid   = median(surviving prices)          # NOT the all-quotes median
spread    = (max − min) / m_valid
tolerance = max(tolerance_pct, min_absolute_tolerance / m_valid)
agrees    = spread <= tolerance and not deadlocked
```

The denominator is the median of the **survivors**. Using the all-quotes median leaves
the tolerance test scaled by a value the outlier just contaminated.

**The tick floor.** A tolerance below one minimum price increment makes every legal
one-tick disagreement a breach. Under Reg NMS Rule 612 an NMS stock quoted at or above
$1.00 currently moves in $0.01 increments, so one tick is `0.01 / price`, which exceeds
5 bps for any stock under $20. The 5 bps default therefore mis-fires across most of the
sub-$20 universe unless `min_absolute_tolerance` is set to the instrument's increment.

**Boundary behaviour.** The rule is `<=`, so a spread exactly at tolerance is
agreement. Do not calibrate to the last bit: decimal thresholds are not exactly
representable in binary floating point, and `(100.025 − 99.975) / 100.0` evaluates to
`0.0005000000000001136`, which is *not* `<= 0.0005`.

## 5. Resolution

| Condition | Price | `status` | `is_cross_verified` |
|---|---|---|---|
| Single usable quote | that quote | `RECONCILIATION_UNCORROBORATED` | `False` |
| ≥2 survivors, agree | reliability-weighted average | `RECONCILIATION_SUCCESS` | `True` |
| 1 survivor after filtering | that quote | `RECONCILIATION_UNCORROBORATED` | `False` |
| ≥2 survivors, disagree | tie-breaker winner | `RECONCILIATION_UNRESOLVED` | `False` |
| Filter deadlocked | tie-breaker winner | `RECONCILIATION_UNRESOLVED` | `False` |
| `WEIGHTED_AVERAGE` method, disagree | composite | `RECONCILIATION_UNRESOLVED` | `False` |
| All quotes stale | `None` | `RECONCILIATION_NO_USABLE_QUOTE` | `False` |

`RECONCILIATION_SUCCESS` is the only outcome that implies corroboration. Callers should
branch on `is_cross_verified` rather than truth-testing the status string.

## 6. Deterministic tie-breaking

Each rule is expressed as `min()` over a key that is a **total order ending in
`vendor_id`**, so the winner is identical no matter how the caller ordered the list:

| Method | Key | Meaning |
|---|---|---|
| `PRIORITY` | `(vendor_priority, −timestamp, vendor_id)` | Lowest rank number, then freshest, then lexicographic |
| `FRESHNESS` | `(−timestamp, vendor_priority, vendor_id)` | Newest, then best rank, then lexicographic |
| `VOLUME_WEIGHTED` | `(−volume_depth, vendor_priority, vendor_id)` | Deepest book, then best rank, then lexicographic |

Without the trailing `vendor_id`, three quotes with equal priority, timestamp and depth
produce three different winners depending on list order — reproduced against version 1.x,
which returned `ALPHA`, `MIKE` or `ZULU` for the same three quotes rotated.

The composite has the same hazard in a subtler form: floating-point addition is not
associative, so `sum()` over a differently-ordered list can differ in its last bits. The
engine sorts by `vendor_id` before summing and uses `math.fsum`.

**A tie-break is a policy, not a detection.** Preferring Bloomberg when the vendors
disagree records an operator preference. It does not establish that the other vendors
were wrong, and the report must not imply that it did.

An unrecognised method raises at config construction. Version 1.x fell back to
`valid_quotes[0]` under a `DEFAULT_FIRST` label, so a single-character typo in a config
file silently converted the whole pricing stack to order-dependence.

## 7. Audit record

`PriceReconciliationReport` fields and their invariants:

- `valid_quotes_count + outlier_quotes_count + len(stale_quotes) == total_quotes_received`
- `reconciled_quotes`, `outlier_quotes` and `stale_quotes` are the corresponding partitions
- `contributing_vendor_ids` is sorted, and is a single element for any tie-broken price
- `observed_spread_pct` and `effective_tolerance_pct` record what was actually compared
  and against what — `effective_tolerance_pct` reflects the tick floor, so it may exceed
  the configured `tolerance_pct`
- `median_price` is the median of the survivors
- `filter_deadlocked` distinguishes "no outliers found" from "no outlier attributable"

## 8. Version 1.x defects fixed in 2.0.0

Each was reproduced against the previous implementation before being fixed.

| Defect | 1.x observed behaviour |
|---|---|
| `NaN` quote | `canonical_price = nan`, `status = RECONCILIATION_SUCCESS`, 3 valid, 0 outliers |
| Bimodal deadlock | `RECONCILIATION_SUCCESS`, 4 valid, **0 outliers**, after all four were rejected |
| Zero weights | `ZeroDivisionError` |
| Symbol mismatch | MSFT quote priced as AAPL, reported as success |
| Duplicate vendor | Duplicated vendor dominated the median; 2 "valid" quotes from 1 real source |
| Tie-break determinism | Same three quotes rotated produced three different winners |
| Misspelt method | Silently resolved as `DEFAULT_FIRST` (first element of the list) |
| Crypto precision | 0.00002181 rounded to `0.0` by a hardcoded `round(price, 4)` |
| Stale quotes | Timestamps used only as a tie-breaker; a frozen quote inside the deviation band participated in the median, the filter and the composite |
| Trust reporting | `status` was hardcoded to `RECONCILIATION_SUCCESS` on every path |
