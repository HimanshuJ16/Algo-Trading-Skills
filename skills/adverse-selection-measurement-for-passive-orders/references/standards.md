# Execution-Quality Standards — adverse-selection-measurement-for-passive-orders

## 1. Theoretical basis — Glosten & Milgrom (1985)

The bid-ask spread decomposes into an **adverse-selection component** Ψ and an
**order-processing component**. A risk-neutral, competitive market maker quotes
a bid and ask equal to the expected asset value *conditional on* a trader
dealing on that side:

```
A_t = E[V | S_t, Z_t > A_t]     (ask: someone is willing to buy)
B_t = E[V | S_t, Z_t < B_t]     (bid: someone is willing to sell)
```

Informed traders only transact when it benefits them (buying undervalued,
selling overvalued), so the market maker **systematically loses** to them on
every informed trade. The spread is the maker's compensation for that expected
loss. A positive spread exists *even with zero transaction costs and zero
expected maker profit* — it is pure adverse selection.

A **markout** empirically measures Ψ: it is the post-fill price drift, and a
negative drift means the conditional expectation moved *against* the passive
fill — i.e. the fill was on the informed-wrong side. This engine is the
empirical estimator of that component.

## 2. Markout conventions

| Quantity | Formula | Sign |
|---|---|---|
| Markout (Buy), `fill_to_mid` | `(Future_Mid / Fill_Price - 1) * 10000` | `> 0` favorable (price rose after buy) |
| Markout (Sell), `fill_to_mid` | `(Fill_Price / Future_Mid - 1) * 10000` | `> 0` favorable (price fell after sell) |
| Markout (Buy), `arrival_to_mid` | `(Future_Mid / AsOf_Mid - 1) * 10000` | drift from fill-time mid |
| Markout (Sell), `arrival_to_mid` | `(AsOf_Mid / Future_Mid - 1) * 10000` | drift from fill-time mid |

**Positive always = favorable.** Sell-side is *inverted* so the convention is
side-agnostic: a negative curve means adverse selection regardless of side.

Units: **basis points (bps)** normalize across asset prices. Practitioners also
use mils/share (equities) or ticks/contract (derivatives); bps is the
price-agnostic default here.

## 3. Horizon guidance

| Horizon class | What it isolates | Typical values |
|---|---|---|
| Sub-second (10–100 ms) | Stale-quote / latency arbitrage; feed-handler + cancellation latency | 10 ms, 50 ms, 100 ms |
| Short (1–5 s) | Immediate informed-flow selection; queue-position toxicity | 1 s, 5 s |
| Medium (10–60 s) | Short-horizon alpha decay vs adverse selection | 10 s, 30 s, 60 s |
| Long (5–30 min) | Directional adverse selection; alpha-vs-friction separation | 300 s, 1800 s |

**Rule:** always span at least two orders of magnitude. A single horizon
collapses latency-arbitrage toxicity and directional toxicity into one number
and cannot diagnose either. A short-negative / long-positive curve is a
fundamentally different problem from a uniformly negative curve.

## 4. Markout bases

| Basis | Reference price | Isolates | Use when |
|---|---|---|---|
| `fill_to_mid` (default) | The actual fill price | Execution quality (spread capture + adverse selection combined) | Evaluating the strategy's realized execution quality |
| `arrival_to_mid` | The fill-time mid (as-of, no lookahead) | Pure post-fill adverse drift, independent of fill price | Diagnosing adverse selection separate from price improvement |

`fill_to_mid` conflates price improvement (you filled better than mid) with
adverse selection (mid drifted against you). `arrival_to_mid` removes the
price-improvement term and isolates the drift. Compare both: a negative
`arrival_to_mid` with a positive `fill_to_mid` means you earn the spread but
still leak to informed flow.

## 5. Lookahead discipline

Two lookahead traps, both closed by the engine:

1. **Fill-time as-of mid.** If the market series starts *after* a fill, the
   nearest-mid search would use a future price as the reference. The
   `require_asof_mid` guard requires a market timestamp `≤ fill_ts`; otherwise
   the fill is skipped and recorded in `missing_pre_fill`. Never disable this
   in backtests.
2. **Horizon truncation.** If `fill_ts + horizon` exceeds the last market
   timestamp, the legacy engine clamped to the last price — fabricating a
   `0 bps` markout and hiding truncation. This engine returns `None` and
   records the fill in `stats[h].truncated`. Always extend the market-data
   window by `max(horizons)` forward so truncation is zero on a full sample.

## 6. Aggregation and distribution

For each horizon the engine reports a distribution, not just the mean:

| Stat | Why |
|---|---|
| `mean_bps` | Backward-compatible headline; sensitive to tails |
| `median_bps` | Robust to the few badly-selected fills that dominate the mean |
| `p25_bps`, `p75_bps` | Inter-quartile range; spread of the toxicity |
| `std_bps` | Dispersion; high std + negative mean = heterogeneous flow |
| `count` | Denominator; < 30 → trust median not mean |
| `truncated` | Data-availability audit; must be 0 on a clean window |

`quantity_weighted=True` weights each fill by its quantity (share-weighted),
matching the practitioner convention (mils/share, notional weighting). Large
fills selected differently from small ones flip the weighted vs unweighted
verdict — route that signal to `queue-position-modeling-for-passive-orders`.

## 7. Toxicity verdict

`is_toxic = (negative-mean horizons) > (total horizons / 2)` — a coarse
majority gate. Always read it alongside:

- `toxicity_ratio` — the fraction of negative horizons (granularity the boolean
  loses).
- The per-horizon curve — *which* horizons are negative diagnoses *what kind*
  of toxicity (latency vs directional, see SKILL.md Decision Points).
- The distribution — a median-positive / mean-negative split is not "toxic" in
  the robust sense.

A single negative horizon among six is not toxic; a uniformly negative curve
across two orders of magnitude is.

## Category

`execution-quality` — see top-level `mappings/` directory.
