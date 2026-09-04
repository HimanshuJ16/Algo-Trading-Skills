---
name: adverse-selection-measurement-for-passive-orders
description: >-
  Use when passive fills keep happening just before the market moves against you,
  measuring forward markouts in basis points against the prevailing mid at each horizon.
  Aggressive orders pay the spread instead and are measured elsewhere.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: market-microstructure-latency
  tags: execution, trading, adverse-selection, markouts, market-microstructure, execution-quality, no-lookahead
  brokers_frameworks: generic
  version: "1.3.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this skill to evaluate the execution quality of **passive / liquidity-
providing** algorithms — market makers, passive limit strategies, and any
resting-order logic. If your fills consistently happen right before the market
moves against you (you buy, price drops; you sell, price rises), you are
suffering **adverse selection**: informed flow is picking off your resting
quotes, and you are writing a free option to the informed (Glosten & Milgrom,
1985).

The skill produces a `MarkoutEngine` that computes, for each fill and each
forward horizon, the basis-point price drift from the fill (or from the fill-
time mid) to the future mid, then aggregates into a per-horizon markout curve
with distribution statistics. A persistently negative curve = toxic flow.

## When NOT to Use

- **Active / aggressing orders** (market orders, crossing sweeps). Adverse
  selection is a *passive* phenomenon — you were resting and got picked off.
  Active orders pay the spread up front; measure that with `execution-slippage-
  attribution-timing-vs-sizing` or `implementation-shortfall-minimization`.
- **Latency-arbitrage diagnostics.** A sharp negative markout in the first
  1–10 ms is a *symptom* of stale-quote latency arbitrage against you, but the
  fix lives in the feed-handler/cancellation path (`tick-to-trade-latency-
  measurement`), not here. Use this skill to *detect*, then route to the
  latency skill to *remediate*.
- **Alpha / signal research.** Markouts measure execution friction, not
  predictive power. For signal strength use `backtest-reporting-standardized-
  tearsheet` or factor research skills.
- **Markets without a clean mid** (some OTC, illiquid single-name options with
  wide stale quotes). The mid-to-mid markout is only as honest as the mid;
  a stale or gappy mid produces nonsense markouts. Use `multi-source-price-
  reconciliation-tie-breaking` to fix the mid first.
- **Tick-by-tick attribution of a handful of fills.** With < ~30 fills per
  horizon the mean is noisy; report the distribution (median, IQR) and do not
  over-interpret the sign of the mean.

## Prerequisites

- Python 3.10+, `numpy`.
- A **ledger of passive fills** — each a `(trade_id, timestamp, side, fill_price,
  quantity)`. Active/aggressing fills must be filtered out upstream; this engine
  assumes every fill it receives is passive.
- A **high-resolution mid-price series** `(timestamp, mid)` covering every fill
  timestamp *and* `max(horizons)` seconds forward. Timestamps must be strictly
  ascending, finite, with positive mids (validated at evaluation time).
- **Clock alignment** between the fill ledger and the market-data series —
  same epoch, same time base. See `clock-skew-correction-for-tick-timestamps`.

## Workflow

1. **Filter to passive fills.** Exclude market/aggressing orders upstream;
   adverse selection only applies to resting liquidity.

2. **Configure horizons and basis:**

   ```python
   from adverse_selection_measurement_for_passive_orders import (
       MarkoutEngine, MarkoutConfig, PassiveFill,
   )

   config = MarkoutConfig(
       horizons_sec=[0.1, 1.0, 5.0, 60.0],   # 100ms, 1s, 5s, 1m
       markout_basis="fill_to_mid",          # or "arrival_to_mid" (mid-to-mid)
       quantity_weighted=True,               # share-weighted MEAN only
       require_asof_mid=True,                # no-lookahead guard, both bases
       max_mid_staleness_sec=None,           # set it; see step 2b
   )
   engine = MarkoutEngine(config)
   ```

   **Set the staleness bound deliberately.** `max_mid_staleness_sec` is `None`
   (off) by default because no universal value exists — the engine will not
   invent one. Derive it from the instrument's own quote-update cadence: a
   bound well above the typical inter-quote gap catches only session gaps,
   halts and dead feeds; a bound near that gap discards live data. Left unset,
   a markout can be measured against a mid from before a halt and still look
   like clean evidence.

3. **Evaluate:**

   ```python
   report = engine.evaluate_fills(fills, market_timestamps, market_mids)
   ```

4. **Check the data verdict before the toxicity verdict.**
   - `report.has_sufficient_data` / `report.evaluable_horizons` — how many
     horizons actually produced a markout. **If this is 0, `is_toxic=False`
     means "not measured", not "healthy"** — the message reads
     `INSUFFICIENT DATA`. Never gate on `is_toxic` without checking this first.
   - `report.missing_pre_fill` — fills skipped by the no-lookahead guard.
   - `report.stale_asof_mid` — fills skipped because the fill-time mid was
     older than `max_mid_staleness_sec`.
   - `report.stats[h].truncated` — fills dropped at horizon `h` because the
     market data did not cover `fill_ts + h`.
   - `report.stats[h].stale` — fills dropped at `h` because the prevailing mid
     there was over-age.

   Then read the curve:
   - `report.average_markouts_bps[h]` — mean markout per horizon (backward-compat).
   - `report.stats[h]` — `count, mean, median, p25, p75, std, truncated, stale`.
     Only `mean_bps` honours `quantity_weighted`; the median, quartiles and std
     are always **unweighted** order statistics.
   - `report.is_toxic` — True if a *majority of the evaluable horizons* have a
     negative mean. Horizons with no data are excluded entirely rather than
     counted as healthy.
   - `report.toxicity_ratio` — negative share of the **evaluable** horizons.

5. **Diagnose the shape** (see Decision Points):
   - Sharp negative in the first 100 ms → stale-quote / latency arbitrage.
   - Gradual negative over seconds–minutes → directional adverse selection
     (your alpha is wrong or you are on the wrong side of informed flow).
   - Positive at short horizons, negative later → you capture the spread but
     bleed to informed flow over the holding period.

## Decision Points

| Situation | Action |
|-----------|--------|
| Curve sharply negative in first 100 ms | Stale-quote latency arbitrage. Tighten feed-handler / cancellation latency (`tick-to-trade-latency-measurement`); widen quote skew in fast markets. |
| Curve slopes negative over 1–5 min | Directional adverse selection — your alpha or quote side is wrong. Re-examine the signal, not the plumbing. |
| Positive short, negative long horizon | You earn the spread but leak to informed flow. Consider faster scratch-outs / hedging, or shorter holding period. |
| `evaluable_horizons == 0` / `has_sufficient_data` False | **Not a healthy-flow result.** Nothing was measured. Extend the market-data window and re-run before drawing any conclusion; never feed this verdict to a gate. |
| Some horizons evaluable, others not | The verdict covers the evaluable ones only. Check `stats[h].truncated` / `.stale` per horizon before comparing the curve day-over-day — the shape is not comparable across different evaluable sets. |
| `stale_asof_mid > 0` or `stats[h].stale > 0` | The prevailing mid was older than `max_mid_staleness_sec` — a session gap, halt, or dead feed. Do not lower the bound to make the count go away; fix the feed or exclude the period. |
| `missing_pre_fill > 0` | Some fills have no as-of mid (market data starts after the fill). Extend the market-data window backward, or fix clock alignment (`clock-skew-correction-for-tick-timestamps`). |
| `stats[h].truncated > 0` | Market data ends before `fill_ts + h`; the horizon's mean is computed on the *surviving* fills only and is biased. Extend the window forward by `max(horizons)`. |
| Mean negative but median positive | Bimodal: a few badly-selected fills dominate the mean. Inspect the distribution; consider a robust threshold on the median, not the mean. |
| `quantity_weighted` flips the verdict vs unweighted | Large fills are being selected differently from small ones. Route the size dimension to `queue-position-modeling-for-passive-orders`. |
| `arrival_to_mid` curve negative but `fill_to_mid` positive | Your fills are *better* than mid (price improvement) but the mid drifts against you afterward — pure adverse selection on the resting side. |
| Few fills (<30/horizon) | Distribution is noisy. Report median + IQR; do not gate on the mean sign. |

## Common Pitfalls

- **Fabricating future mids by clamping to the last price.** The legacy engine
  returned the last known price when a horizon exceeded the data, silently
  producing a markout of `(last/last - 1)*10000 = 0` and *hiding* truncation.
  This engine returns `None` and records `truncated` per horizon — never
  silently clamp. Always extend the market-data window by `max(horizons)`.
- **Snapping the horizon mid to the nearest quote.** Nearest-in-time lookup can
  resolve to an observation *after* `fill_ts + h`, silently lengthening the
  effective horizon: with a 1.9 s gap in the mid series a "100 ms markout" is
  really a 1.9 s markout, and the latency-vs-directional diagnosis this skill
  exists to make collapses. This engine uses the **prevailing** mid — the last
  observation at or before the target — for both the reference and every
  horizon. If you reimplement the lookup, use an as-of join, not nearest.
- **Lookahead in the fill-time mid.** If the market series starts *after* a
  fill, a nearest-mid search silently uses a *future* price as the as-of mid.
  The `require_asof_mid` guard skips such fills and records `missing_pre_fill`.
  It applies under **both** bases: under `fill_to_mid` the as-of mid is not the
  reference price, but its absence means the fill precedes the data window and
  every horizon would be measured against unrelated later quotes. Never disable
  it for backtests.
- **Trusting a stale mid.** A mid carried across a session gap, a halt or a
  dead feed is fabricated in exactly the way a clamped one is — it just fails
  silently instead of loudly. `max_mid_staleness_sec` refuses it and records
  `stats[h].stale` / `stale_asof_mid`. Setting the bound is your job; the
  engine has no defensible universal default and will not guess one.
- **Reading a no-data verdict as healthy.** `is_toxic=False` with
  `evaluable_horizons == 0` means *nothing was measured*, not that flow is
  clean. Check `has_sufficient_data` before acting; the message says
  `INSUFFICIENT DATA` precisely so a truncated run cannot be mistaken for a
  clean bill of health by a downstream gate or an agent skimming the boolean.
- **Letting an unmeasurable horizon vote.** A horizon with no data is excluded
  from `toxicity_ratio` and `is_toxic` rather than counted as non-toxic —
  otherwise a short window silently dilutes a genuinely toxic curve toward
  "healthy".
- **Including active orders.** Adverse selection is a passive phenomenon.
  Aggressing fills pay the spread up front and their "markout" conflates spread
  cost with adverse selection. Filter upstream.
- **Directional sign error on sells.** Sell markout is *inverted*
  (`fill_price/future_mid - 1`); a positive value means price fell after you
  sold (favorable). Forgetting the inversion flips the entire sell-side curve.
- **Assuming `quantity_weighted` weights the whole distribution.** It weights
  `mean_bps` only. `median_bps`, `p25_bps`, `p75_bps` and `std_bps` stay
  unweighted order statistics — so the documented "gate on the median when
  `count < 30`" rule gates on an *unweighted* median even with weighting on.
- **Confusing `arrival_to_mid` with arrival-price benchmarking.** Here it means
  the **fill-time** mid (the industry's *mid-to-mid* markout), not the price at
  order arrival or decision time. For a true arrival-price benchmark use
  `implementation-shortfall-minimization`.
- **Mean-only reporting.** A negative mean driven by a fat-tailed minority of
  badly-selected fills hides a healthy median. Always report the distribution
  (`median`, `p25`, `p75`), especially with few fills.
- **Unsorted / duplicate market timestamps.** `bisect` / `searchsorted` require
  strictly ascending timestamps. The engine validates and raises; if you
  pre-process externally, preserve the invariant.
- **Mismatched clocks.** Fill timestamps and market timestamps must share an
  epoch and time base. A clock skew of even tens of ms corrupts sub-second
  markouts.
- **EOD-only measurement.** Microstructure toxicity lives in ms-to-seconds.
  Measuring only at EOD hides execution friction under alpha decay. Always
  include short (≤1 s) horizons.
- **Over-interpreting a toxic flag.** `is_toxic` means a *majority* of horizons
  are negative — it is a coarse summary. Read `toxicity_ratio` and the per-
  horizon curve; one negative horizon among six is not "toxic".

## Verification

Run the unit tests:

```bash
python -m unittest discover -s skills/adverse-selection-measurement-for-passive-orders/scripts -v
```

What they assert:

- Toxic buy → negative markout; profitable sell → positive markout; mixed fills
  compute the correct net curve and toxicity ratio.
- Rising market buy is profitable (sign convention correct).
- No-lookahead: a fill before the market series is skipped + recorded as
  `missing_pre_fill`; as-of mid uses the most recent *pre-fill* sample.
- The no-lookahead guard applies under **both** bases: a `fill_to_mid` fill
  preceding the market window is skipped and counted in `missing_pre_fill`;
  with the guard disabled the horizon is still truncated, never fabricated.
- The horizon mid is the **prevailing** one, not the nearest: a mid series that
  jumps at t=1.9 s leaves a 1 s markout at 0 bps, and short and long horizons
  stay separable across a later price move.
- Truncation: a horizon beyond the data returns `None` and is recorded, never
  clamped to the last price.
- Staleness: an over-age prevailing mid is refused and counted in
  `stats[h].stale` / `stale_asof_mid`, distinctly from truncation; the bound is
  off by default; non-positive or non-finite bounds raise.
- Verdict integrity: a fully truncated run reports `INSUFFICIENT DATA` with
  `has_sufficient_data` False, and a truncated horizon does not dilute
  `toxicity_ratio` (one measured negative horizon out of two reads 1.0/toxic,
  not 0.5/healthy).
- A non-string `side` raises `ValueError`, not `AttributeError`.
- Config validation: empty/duplicate/non-positive horizons, invalid basis,
  horizons sorted on construction.
- Fill validation: bad side, non-positive price/quantity, side case normalized.
- Market-data validation: length mismatch, empty, non-finite, non-positive
  mid, unsorted/duplicate timestamps.
- Quantity-weighting changes the mean when quantities differ across price
  regimes; no-op when they don't.
- `arrival_to_mid` basis uses the as-of mid, not the fill price.
- Distribution stats (`count, mean, median, p25, p75, std, truncated`) populated.
- Disabled engine and empty fills return clean empty reports; `as_dict()`
  round-trips through JSON.

Confirm with the operational checklist in `assets/checklist.md` before acting
on a toxicity verdict.

## Success Criteria

A markout measurement program is **healthy in production** when:

1. The market-data window extends `max(horizons)` forward and a backward buffer
   before the first fill, so `missing_pre_fill`, `stale_asof_mid` and all
   `truncated` / `stale` counters are zero on a full-day sample, and
   `evaluable_horizons == len(horizons_sec)`.
2. `is_toxic` is computed from a curve spanning at least three horizons across
   two orders of magnitude (e.g. 100 ms, 1 s, 10 s) — a single-horizon verdict is
   brittle.
3. Each horizon's report includes the distribution (`median`, `p25`, `p75`),
   not just the mean; gating uses the median when `count < 30`.
4. `quantity_weighted=True` is the default for notional-aware aggregation;
   unweighted is recorded for comparison.
5. `missing_pre_fill == 0` and `has_sufficient_data` is True, verified daily (a
   non-zero skip count means clock skew, a stale feed, or a truncated window).
   No gate, alert or kill-switch input reads `is_toxic` without first checking
   `has_sufficient_data`.
6. The verdict is reproducible from the frozen fill ledger + market-data
   snapshot (same inputs → same `as_dict()`).

## Related Skills

- `post-trade-execution-quality-scorecard` — broader TCA scorecard of which
  markouts are one component.
- `execution-slippage-attribution-timing-vs-sizing` — separates slippage into
  timing and sizing components; markouts attribute the timing/adverse side.
- `implementation-shortfall-minimization` — active-order counterpart; this
  skill is the passive-order counterpart.
- `queue-position-modeling-for-passive-orders` — explains *why* large passive
  fills are selected differently; pair with `quantity_weighted` analysis.
- `tick-to-trade-latency-measurement` — remediates the short-horizon toxic
  curve (stale-quote latency arbitrage) this skill detects.
- `clock-skew-correction-for-tick-timestamps` — the clock alignment this skill
  assumes; non-zero `missing_pre_fill` often traces here.
- `kill-switch-and-drawdown-circuit-breakers` — a persistently toxic curve is a
  candidate trigger for a strategy-level kill switch.
- `real-time-liquidity-risk-monitoring` — live complement to this post-trade
  measurement.
