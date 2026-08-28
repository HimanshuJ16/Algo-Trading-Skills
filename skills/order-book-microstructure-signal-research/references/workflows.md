# Workflows for Order Book Microstructure Signal Research

Full procedure behind `SKILL.md`. Every threshold named here is this skill's engineering
choice — see `references/standards.md` for what is published and what is not.

## 1. Validate the series before computing anything

`extract_features` rejects six input classes outright rather than repairing them,
because each otherwise yields a confidently wrong report instead of an error:

| Rejected | Why repairing it would hide the problem |
|---|---|
| Non-finite price or size (NaN/Inf) | NaN compares `False` against every threshold. It propagates a NaN IC, `NaN >= 0.05` is `False`, and the run reports a clean `WEAK_SIGNAL` as if measured. |
| Negative size | There is no such queue. It also flips the sign of `voi` and of the total-depth guard. |
| Non-positive price | Simple mid-to-mid returns are undefined through zero and sign-flip through a negative denominator. Negatively-priced instruments exist (CME WTI, 2020-04-20) and need a different return convention. |
| Crossed book (`ask < bid`) | Spread goes negative, so `micro_price_dev` inverts sign relative to `voi` on exactly those ticks. Consolidated/NBBO feeds legitimately cross; clean them upstream. |
| Out-of-order timestamps | `e_n` differences observation `n` against `n-1`. Unsorted ticks describe events that never happened. |
| Mixed symbols | The report is labelled with `ticks[0].symbol`. Interleaving two books produces price deltas between unrelated instruments. |

Equal timestamps are accepted: same-nanosecond updates are real, and TAQ-style sources
round timestamps coarsely. A locked book (`ask == bid`) is accepted and produces a
`micro_price_dev` of exactly zero.

## 2. Extract per-tick features

For each tick, with $Q = q^B + q^A$:

- Mid $M = (P^B + P^A)/2$, spread $S = P^A - P^B$.
- Depth volume imbalance $VOI = (q^B - q^A)/Q$, in $[-1, +1]$.
- Weighted mid $P^w = (q^B P^A + q^A P^B)/Q$, and $P^w - M$, which equals $\frac{VOI}{2}S$ exactly.
- When $Q = 0$ the imbalance is undefined: $P^w$ falls back to $M$, $VOI$ to 0, and the tick is counted in `degenerate_depth_ticks` and flagged `DEGENERATE_DEPTH_TICKS`.
- Order flow imbalance $e_n$, all six branches:

| Condition | Bid contribution | Condition | Ask contribution |
|---|---|---|---|
| $P^B_n > P^B_{n-1}$ | $+q^B_n$ (price-improving limit buy) | $P^A_n < P^A_{n-1}$ | $-q^A_n$ (price-improving limit sell) |
| $P^B_n = P^B_{n-1}$ | $q^B_n - q^B_{n-1}$ (add / market sell / cancel) | $P^A_n = P^A_{n-1}$ | $q^A_{n-1} - q^A_n$ (add / market buy / cancel) |
| $P^B_n < P^B_{n-1}$ | $-q^B_{n-1}$ (**bid queue removed entirely**) | $P^A_n > P^A_{n-1}$ | $+q^A_{n-1}$ (**ask queue removed entirely**) |

$e_n$ is the sum of the two contributions. The bold rows are the ones v1.0.0 assigned
zero.

Row 0 carries `is_event_observed=False` and `ofi=0.0`: `e_0` has no predecessor and is
undefined, not zero.

Features are returned **unrounded**. Rounding a signal inside the data structure destroys
it on fractional-quantity and high-decimal-precision instruments.

## 3. Aggregate into the published variable, if you want it

$e_n$ is one event's contribution. $OFI_k = \sum e_n$ over an interval is what Cont,
Kukanov and Stoikov regress against price changes. `ofi_window_ticks` sets the rolling
sum length:

- `1` (default): the signal tested is the raw per-event $e_n$.
- `> 1`: the signal is the rolling sum over that many *observed* events. Rows before the
  window fills carry `is_window_complete=False` and are excluded from the sample — a sum
  over 2 events is a different random variable from a sum over 5.

## 4. Form the research sample

Usable indices run from `max(1, ofi_window_ticks)` to `n_ticks - 1 - k` inclusive:

- Index 0 is excluded (undefined `e_0`).
- The first `ofi_window_ticks` rows are excluded (incomplete window).
- The last `k` rows are excluded (no forward tick to measure against).

For each usable index $i$: forward return $R_{i} = (M_{i+k} - M_i)/M_i$, with **both**
endpoints at full precision. Rounding the current mid but not the forward mid biases
every return in the same direction.

There is no look-ahead: the feature at $i$ reads only ticks $\le i$, and the return reads
only ticks $\ge i$. A `forward_horizon_ticks` below 1 is rejected at construction — 0
zeroes every return, and a negative value silently turns the study into a look-back that
manufactures correlation out of contemporaneous information.

## 5. Compute the statistics

- **IC**: Pearson correlation of the signal against forward returns, for the aggregated
  OFI, the weighted-mid deviation and VOI. Pearson (rather than Spearman) is appropriate
  because the published relation is linear and a quadratic term adds ~3 points of $R^2$
  insignificantly. A zero-variance series yields 0.0, flagged `ZERO_VARIANCE_SIGNAL` —
  that zero means *undefined*, not *uncorrelated*.
- **Effective sample**: $n_{\text{eff}} = \lfloor N/k \rfloor$. Overlapping returns are
  not independent observations.
- **t-statistic**: $t = IC\sqrt{(n_{\text{eff}}-2)/(1-IC^2)}$. Conservative, not HAC.
- **Hit ratio**: over ticks where the signal *and* the forward return are both non-zero.
  Everything else lands in `flat_or_neutral_ticks`. Always read the hit ratio against
  `directional_predictions`; a high ratio over 5% of the sample is a statement about 5%
  of the sample.

## 6. Interpret the findings

| Finding | Meaning | First thing to check |
|---|---|---|
| `INSUFFICIENT_EFFECTIVE_SAMPLE` | Fewer than 30 non-overlapping observations. | Capture more data, or shorten the horizon. |
| `IC_BELOW_FLOOR` | Signed IC under 0.05. | Whether the horizon matches the mechanism you expect. |
| `IC_SIGN_INVERTED` | IC at or below −0.05. | Bid/ask column swap on feed load, quantity sign convention, timestamp misalignment. Only then a contrarian regime. |
| `HIT_RATIO_BELOW_FLOOR` | Under 53% of directional calls correct. | Whether `directional_predictions` is large enough to mean anything. |
| `NO_DIRECTIONAL_PREDICTIONS` | Every tick was flat on the signal or the outcome. | Whether the series has any activity at the top of book at all. |
| `ZERO_VARIANCE_SIGNAL` | Signal or return series is constant; IC undefined. | Whether the capture is a stalled feed rather than a quiet market. |
| `DEGENERATE_DEPTH_TICKS` | One or more ticks had zero total top-of-book depth. | Feed gaps, session boundaries, or a genuinely empty book. |
| `CONSTANT_SPREAD_COLLINEARITY` | Spread is constant, so `ic_micro_price_dev_return` equals `ic_voi_forward_return`. | Do not report them as two agreeing signals. |

## 7. Verdict

| Status | Condition |
|---|---|
| `PREDICTIVE_ALPHA_FOUND` | IC $\ge$ 0.05 **and** at least one directional call **and** hit ratio $\ge$ 53% **and** $n_{\text{eff}} \ge$ 30. |
| `INSUFFICIENT_SAMPLES` | Not approved, and $n_{\text{eff}} <$ 30. Not enough independent observations to say either way. |
| `WEAK_SIGNAL` | Not approved, with enough observations to have measured it. |

A `PREDICTIVE_ALPHA_FOUND` verdict is a statistical statement about mid-price movement.
It is not a claim that the move survives the spread, fees, queue position and latency —
take that question to `transaction-cost-analysis-tca-integration` and
`queue-position-modeling-for-passive-orders` before allocating capital.
