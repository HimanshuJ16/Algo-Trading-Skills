---
name: adverse-selection-measurement-for-passive-orders
description: Post-trade markout engine for passive (resting limit) fills. Computes
  forward-horizon markouts in basis points across a sorted, lookahead-safe market
  mid series to quantify adverse selection (toxic flow). Per-horizon distribution
  stats, quantity-weighting, fill-to-mid and arrival-to-mid bases, no-lookahead
  as-of guard, and explicit truncation accounting. Flags a curve as toxic when a
  majority of horizons are negative.
domain: algorithmic-trading
subdomain: execution-quality
tags:
- execution
- trading
- adverse-selection
- markouts
- market-microstructure
- execution-quality
- no-lookahead
brokers_frameworks:
- generic
jurisdictions: [global]  # technique is jurisdiction-agnostic
version: "1.2.0"
author: algo-trading-skills-contributors
license: Apache-2.0
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
  attribution-timing-vs-sizing` or `arrival-price-benchmark-execution-algo`.
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

- Python 3.9+, `numpy`.
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
       markout_basis="fill_to_mid",          # or "arrival_to_mid"
       quantity_weighted=True,               # share-weighted mean
       require_asof_mid=True,                # no-lookahead guard
   )
   engine = MarkoutEngine(config)
   ```

3. **Evaluate:**

   ```python
   report = engine.evaluate_fills(fills, market_timestamps, market_mids)
   ```

4. **Read the curve.**
   - `report.average_markouts_bps[h]` — mean markout per horizon (backward-compat).
   - `report.stats[h]` — `count, mean, median, p25, p75, std, truncated`.
   - `report.is_toxic` — True if a *majority* of horizons have negative mean.
   - `report.toxicity_ratio` — fraction of horizons that are negative.
   - `report.missing_pre_fill` — fills skipped by the no-lookahead guard.
   - `report.stats[h].truncated` — fills dropped at horizon `h` because the
     market data ended before `fill_ts + h`.

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
- **Lookahead in the fill-time mid.** If the market series starts *after* a
  fill, the nearest-mid search silently uses a *future* price as the as-of mid.
  The `require_asof_mid` guard skips such fills and records `missing_pre_fill`.
  Never disable it for backtests.
- **Including active orders.** Adverse selection is a passive phenomenon.
  Aggressing fills pay the spread up front and their "markout" conflates spread
  cost with adverse selection. Filter upstream.
- **Directional sign error on sells.** Sell markout is *inverted*
  (`fill_price/future_mid - 1`); a positive value means price fell after you
  sold (favorable). Forgetting the inversion flips the entire sell-side curve.
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
- Truncation: a horizon beyond the data returns `None` and is recorded, never
  clamped to the last price.
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
   before the first fill, so `missing_pre_fill` and all `truncated` counters are
   zero on a full-day sample.
2. `is_toxic` is computed from a curve spanning at least three horizons across
   two orders of magnitude (e.g. 100 ms, 1 s, 10 s) — a single-horizon verdict is
   brittle.
3. Each horizon's report includes the distribution (`median`, `p25`, `p75`),
   not just the mean; gating uses the median when `count < 30`.
4. `quantity_weighted=True` is the default for notional-aware aggregation;
   unweighted is recorded for comparison.
5. `missing_pre_fill == 0` is verified daily (a non-zero count means clock skew
   or a truncated data window).
6. The verdict is reproducible from the frozen fill ledger + market-data
   snapshot (same inputs → same `as_dict()`).

## Related Skills

- `post-trade-execution-quality-scorecard` — broader TCA scorecard of which
  markouts are one component.
- `execution-slippage-attribution-timing-vs-sizing` — separates slippage into
  timing and sizing components; markouts attribute the timing/adverse side.
- `arrival-price-benchmark-execution-algo` — active-order counterpart; this
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
