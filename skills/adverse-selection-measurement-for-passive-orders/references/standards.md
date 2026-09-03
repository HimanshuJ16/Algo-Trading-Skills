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

### Which mid is "the mid at t+h"

The horizon mid is the **prevailing** mid: the last observation at or before
`fill_ts + h`. This is an as-of match, and it is the convention practitioner
tooling implements — QuestDB's markout cookbook resolves each horizon with an
ASOF match at `trade_timestamp + offset`.

Nearest-in-time snapping is **not** an acceptable substitute. It can resolve to
an observation *after* the target, which lengthens the effective horizon by up
to half the inter-quote gap and makes the effective horizon a function of the
data's sampling rather than of the configured horizon. Since the entire
diagnostic value of a markout *curve* is that different horizons isolate
different failure modes (section 3), an effective horizon that drifts with the
data destroys the diagnosis: a 1.9 s gap turns a 100 ms markout into a 1.9 s one.

The as-of convention cannot overshoot, but it can reach arbitrarily far *back* —
across a session gap, a halt, or a dead feed. That is what
`max_mid_staleness_sec` bounds (section 5).

### Naming caveat

`arrival_to_mid` is this engine's name for what the practitioner literature
calls the **mid-to-mid** markout: the reference is the **fill-time** mid. It is
*not* an arrival-price benchmark in the implementation-shortfall sense (the
price at order arrival or decision time) — for that see
`arrival-price-benchmark-execution-algo`.

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

## 5. Refusal discipline — never fabricate a mid

Every way a markout can be manufactured out of absent data, and how the engine
refuses instead. The unifying rule: **a missing mid is reported, never
invented.**

| Trap | What a naive engine does | What this engine does | Counter |
|---|---|---|---|
| Market series starts after the fill | Uses a *future* price as the as-of reference (lookahead) | Skips the fill; the `require_asof_mid` guard requires an observation at or before `fill_ts`, under **both** bases | `missing_pre_fill` |
| `fill_ts + h` past the last timestamp | Clamps to the last price, fabricating a `0 bps` markout and hiding the gap | Returns `None` for that horizon | `stats[h].truncated` |
| `fill_ts + h` before the first timestamp | Clamps to the first price — the mirror of the above, and the one usually left open | Returns `None` for that horizon | `stats[h].truncated` |
| Prevailing mid carried across a session gap, halt or dead feed | Measures against an arbitrarily old price that looks like a real observation | Refuses it when `max_mid_staleness_sec` is set | `stats[h].stale`, `stale_asof_mid` |

Notes:

- `require_asof_mid` matters under `fill_to_mid` even though the as-of mid is
  not the reference price there: its absence means the fill precedes the data
  window entirely, so every horizon would be scored against unrelated later
  quotes.
- `max_mid_staleness_sec` defaults to `None` (off). No universal value is
  defensible — it depends on the instrument's quote-update cadence — and the
  repository's rule is that a missing threshold is better than an invented one.
  Set it per instrument; a bound well above the typical inter-quote gap catches
  only genuine outages.
- Always extend the market-data window by `max(horizons)` forward and a buffer
  backward so all four counters are zero on a clean full-day sample.

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
| `stale` | Staleness audit; fills whose prevailing mid at `t+h` was over-age |

`quantity_weighted=True` weights each fill by its quantity (share-weighted),
matching the practitioner convention — Databento notes that equity markouts are
"often share-weighted or notionally-weighted and measured in units of
mils/share or mils/dollar respectively". Large fills selected differently from
small ones flip the weighted vs unweighted verdict — route that signal to
`queue-position-modeling-for-passive-orders`.

**Weighting applies to `mean_bps` only.** `median_bps`, `p25_bps`, `p75_bps`
and `std_bps` remain unweighted order statistics of the per-fill markouts.
This matters for the "gate on the median when `count < 30`" rule above: that
median is unweighted regardless of the `quantity_weighted` setting. A
share-weighted quantile would require a weighted-quantile estimator, which the
engine deliberately does not approximate.

## 7. Toxicity verdict

`is_toxic = (negative-mean horizons) > (evaluable horizons / 2)` — a coarse
majority gate over the horizons that **actually produced a markout**. A horizon
with no data is excluded from both the numerator and the denominator rather
than counted as non-toxic; otherwise a short data window silently dilutes a
genuinely toxic curve toward "healthy" (one measured negative horizon out of
two configured would read 0.5 and pass).

When *no* horizon is evaluable, `evaluable_horizons == 0`,
`has_sufficient_data` is False and the message reads `INSUFFICIENT DATA`.
`is_toxic` is `False` there only because nothing was measured — it is not a
healthy-flow finding, and no gate should consume it without checking
`has_sufficient_data` first.

Always read the verdict alongside:

- `toxicity_ratio` — the fraction of negative horizons (granularity the boolean
  loses).
- The per-horizon curve — *which* horizons are negative diagnoses *what kind*
  of toxicity (latency vs directional, see SKILL.md Decision Points).
- The distribution — a median-positive / mean-negative split is not "toxic" in
  the robust sense.

A single negative horizon among six is not toxic; a uniformly negative curve
across two orders of magnitude is.

## Sources

- Glosten, L. R. & Milgrom, P. R. (1985), "Bid, ask and transaction prices in a
  specialist market with heterogeneously informed traders", *Journal of
  Financial Economics* 14(1), 71-100.
  https://doi.org/10.1016/0304-405X(85)90044-3 — section 1.
- Databento, "What are markouts and how are they used for trading?",
  Microstructure Guide. https://databento.com/microstructure/markout — markout
  definition, the mid-to-mid / trade-to-mid / trade-to-trade bases, curves
  across horizons, and share/notional weighting (sections 2, 3, 6).
- QuestDB, "Post-trade markout analysis" (SQL cookbook).
  https://questdb.com/docs/cookbook/sql/finance/markout/ — the horizon mid is
  resolved by an ASOF match at `trade_timestamp + offset` (section 2).

No regulatory mandate prescribes a markout methodology; the conventions above
are practitioner convention, not a compliance requirement. Where a markout is
used as evidence in a best-execution review, the jurisdiction's own
record-keeping rules apply — see `best-execution-record-keeping-global`.

## Category

`execution-quality` — see top-level `mappings/` directory.
