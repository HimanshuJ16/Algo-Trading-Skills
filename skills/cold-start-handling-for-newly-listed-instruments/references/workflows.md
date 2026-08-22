# Workflows for Cold Start Handling

## 1. Detect the cold start

- Source the listing date from reference data, not from the first row of your own price
  history — a backfill gap looks identical to a new listing otherwise. See
  `instrument-universe-change-detection-and-alerting`.
- Distinguish the three cases that all present as "short history":
  - **New listing** — cold start; this skill applies.
  - **Backfill gap / vendor outage** — a data problem; fix the data, do not shrink.
  - **Halted, suspended or barely traded old instrument** — stale, not young; shrinking
    toward a peer prior hides a liquidity problem behind a plausible number.

## 2. Count usable observations

`n_obs` is **not** `today - list_date`.

- Count bars actually present with a valid price and a computable return.
- Exclude sessions the instrument was halted for in full. LULD pauses in the first
  sessions of a new listing are routine (`references/standards.md`, section 4).
- Exclude the listing auction itself. The IPO cross is price discovery, not a draw from
  the return distribution being estimated.
- Decide once whether `n_obs` counts prices or returns and keep it consistent — `k`
  prices give `k - 1` returns, and the degrees-of-freedom weighting is computed from the
  count you pass.
- If the instrument trades on multiple venues with staggered listing dates, count from
  the venue whose prices you actually use.

## 3. Build the peer prior

- Prefer the **median realized volatility of comparable single names** over a sector ETF's
  volatility. The ETF is diversified and its volatility is structurally lower; using it
  shrinks every new listing toward a level no constituent realizes.
- Match on what drives volatility: sector, market capitalisation, free float, and — for
  IPOs — listing cohort. A recent-IPO cohort is materially more volatile than seasoned
  names in the same sector.
- Freeze the peer-selection rule before the instrument lists. Choosing peers after seeing
  the first prints conditions the prior on the data it is supposed to regularise.
- Recompute the prior on a schedule, point-in-time. A prior computed from today's peer
  set and applied to a backtest of last year's listings is look-ahead; see
  `lookahead-bias-elimination` and `point-in-time-index-constituent-tracking`.
- Choose `nu_0` from the prior's own uncertainty: `nu_0 = 2 / v`, where `v` is the
  relative variance of the prior's estimate of `sigma**2`. A tight, directly comparable
  peer group justifies a larger `nu_0`; a loose sector proxy does not.
- If no defensible peer group exists, **do not trade the instrument through this path**.
  The handler raises on a missing prior on purpose.

## 4. Estimate and size

```python
from cold_start_handler import ColdStartHandler

handler = ColdStartHandler(
    warmup_period_days=30,       # risk policy: when the cap reaches full size
    base_max_position_pct=0.04,  # your normal per-name ceiling
    prior_strength_days=10.0,    # nu_0: the prior is worth 10 days of this name's data
    probation_floor_pct=0.005,   # optional: throttle rather than exclude on day zero
)

status = handler.process_instrument(
    symbol="NEWCO",
    n_obs=5,
    observed_volatility=0.80,      # omit or pass None when n_obs < 2
    peer_prior_volatility=0.45,    # required, finite, strictly positive
)

target = my_vol_target_sizer(status.estimated_volatility)
size = min(target, status.max_position_cap_pct * nav)
```

- `estimated_volatility` feeds the sizer; `max_position_cap_pct` is an independent
  ceiling applied afterwards. The cap is not a target.
- Below two observations the handler ignores `observed_volatility` entirely — including
  `NaN` — and returns the prior with `used_observed_volatility=False`. Log that flag; it
  is how an auditor tells a shrunk estimate from a pure prior.
- Re-run on every rebalance. Caching the day-one status freezes the cap for the whole ramp.

## 5. Graduation and what it does not mean

- At `n_obs >= warmup_period_days` the cap reaches `base_max_position_pct` and
  `is_probationary` becomes False.
- The volatility estimate keeps shrinking, asymptotically. With `nu_0 = 10`, an
  instrument with 250 observations still carries about 4% prior weight. That is intended:
  a finite sample never becomes exact, and a weight that jumped to 1.0 at the warmup
  boundary would put a discontinuity in every instrument's sizing on its graduation day.
- Graduation is not the end of the newness risk. Schedule a review at the events that
  change the float and the regime: lock-up expiry (typically 180 days), first earnings
  report, index addition. A 30-day window measures a float that lock-up expiry will
  change. See `corporate-action-event-calendar-integration`.

## 6. Backtesting this path

- Reconstruct `n_obs`, the peer set and the prior **as of each bar**. A backtest that
  applies today's peer volatilities to a 2021 listing has leaked.
- Include the instruments that failed: names that listed and were delisted, or that never
  graduated, must be in the universe or the cold-start path only ever sees survivors. See
  `survivorship-bias-free-universe-construction`.
- Assert the invariants in the backtest, not only in unit tests: no `NaN` volatility ever
  reaches the sizer, and no position exceeds `max_position_cap_pct` on any bar.
