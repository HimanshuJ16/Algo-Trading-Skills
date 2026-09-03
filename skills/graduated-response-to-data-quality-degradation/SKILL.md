---
name: graduated-response-to-data-quality-degradation
description: >-
  Use when a live trading system must react to degraded market data (stale ticks, sequence gaps, price spikes, crossed books, spread blow-out) with a graduated de-risking mandate — size haircut, block new entries, cancel and flatten — instead of a binary on/off kill.
domain: Real-Time Architecture & Risk
subdomain: Data Quality Monitoring & De-Risking
tags: ["data-quality", "de-risking", "graduated-response", "stale-ticks", "sequence-gaps", "price-spikes", "circuit-breaker"]
brokers_frameworks: ["Generic Feed Quality Telemetry", "Python Standard Library"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in execution systems, feed-handler monitoring, and real-time risk gates that must decide *how much* to trade when the market data is degraded but not obviously dead. It converts per-symbol quality telemetry into a bounded score $Q \in [0, 100]$ and one of four mandates: Tier 0 (full size), Tier 1 (new entries at $50\%$ size), Tier 2 (block new entries, keep exiting), Tier 3 (cancel resting orders and flatten).

Reach for it when a binary healthy/dead switch is too coarse — when a $2$-second staleness blip should shrink size rather than trigger a full stop, and only a crossed book plus a stalled feed should escalate to an emergency halt.

## When NOT to Use

- **As your detector.** This engine *consumes* `stale_time_seconds`, `missing_sequence_count`, `price_spike_anomaly_detected`, `crossed_book_detected` and `bid_ask_spread_multiplier`. It computes none of them. Building those checks is the job of `sequence-number-gap-detection-for-feeds`, `backtest-outlier-and-bad-tick-filtering` and `data-quality-monitoring-dashboard`.
- **As a P&L-driven kill switch.** Data quality and capital loss are independent failure axes, and a system with only one of them is unprotected against the other. Pair with `kill-switch-and-drawdown-circuit-breakers` and `capital-preservation-mode-for-degraded-conditions`.
- **As a latching kill switch.** Tier 3 is a per-observation mandate. It clears on its own once quality recovers (after `recovery_hold_seconds`); it does not require a human to re-arm. If your policy is "an emergency flatten needs an operator to clear it", wire `flatten_positions` into a latching switch that does.
- **As a compliance metric.** No regulator, exchange or standards body publishes a market-data quality score, a tier boundary or a penalty weight. Every number here is a configurable engineering default — see `references/standards.md`.
- **As the execution policy for the flatten itself.** Tier 3 says *reduce exposure*; it does not say "send a market order priced off the feed you just declared unusable". MiFID II RTS 6 Article 14(3) requires that a trading system be shut down "without creating disorderly trading conditions", which is precisely the risk of flattening on corrupt prices.
- **Across processes.** State is in-memory and lock-guarded within one process. Two processes running their own engine keep two independent recovery timers.

## Prerequisites

- A quality collector emitting `DataQualityMetrics` per symbol on a known cadence. `stale_time_seconds` is **required** and has no default: an object built without it would describe a perfect feed.
- An independent heartbeat on that collector. This engine cannot detect its own absence — if nobody calls `audit_and_de_risk`, no tier ever changes.
- An order path that reads the report's booleans, not only `position_sizing_factor`.
- Thresholds and penalties calibrated against your own feed's measured behaviour.

## Workflow

1. **Ingest metrics** — staleness $T_{\text{stale}}$ (age of the last accepted tick), missing sequence count, price-spike flag, crossed-book flag ($\text{Bid} > \text{Ask}$), and spread multiple (current spread / normal spread).
   - **Decision point — a metric that cannot be evaluated is not a clean metric.** A `NaN`, infinite, negative or wrongly-typed value means the collector or feed handler is broken. The engine scores it $0$ and forces Tier 3, setting `metrics_valid=False` and an `INVALID_METRIC:<field>` condition. Never skip an unevaluable penalty: `NaN > 1.0` is `False`, so skipping it returns $Q = 100$ and full trading on unusable telemetry.

2. **Compute the quality score** $Q = 100 - \sum \text{penalties}$, clamped to $[0, 100]$. All five penalties are constructor parameters; the defaults are:
   - Staleness: $-10$ points per second beyond a $1.0\text{s}$ grace.
   - Missing sequence numbers: $-2$ points each.
   - Price-spike anomaly: $-25$ points.
   - Crossed book: $-50$ points.
   - Spread blow-out: $-15$ points per multiple beyond $2.0\times$ normal.
   - **Decision point — classify on the exact score, report the floored one.** Rounding to 2dp *before* the comparison lets a true $89.996$ round to $90.00$ and win `ALLOW_FULL_TRADING`. `data_quality_score_pct` is floored to 2dp so a displayed number can never overstate quality relative to the tier assigned.

3. **Classify the tier** (lower bounds inclusive):
   - $Q \ge 90 \implies$ **Tier 0** `ALLOW_FULL_TRADING`, sizing factor $1.0$.
   - $70 \le Q < 90 \implies$ **Tier 1** `REDUCE_SIZE_50_PCT`, sizing factor $0.50$.
   - $40 \le Q < 70 \implies$ **Tier 2** `BLOCK_NEW_ENTRIES`, sizing factor $0.0$, exits still permitted.
   - $Q < 40 \implies$ **Tier 3** `EMERGENCY_HALT_AND_FLATTEN`, sizing factor $0.0$.
   - **Decision point — do not gate exits on `position_sizing_factor`.** It is $0.0$ at *both* Tier 2 and Tier 3 and is a **new-entry** multiplier only. Gate on `allow_new_entries` / `allow_risk_reducing_exits` / `cancel_resting_orders` / `flatten_positions`.

4. **Apply the recovery hold** (`recovery_hold_seconds`, default $0$ = disabled) — escalate fast, recover slow. A worse tier applies on the observation that produces it; an improvement applies only after the better quality has persisted for the whole hold, and any relapse restarts the timer. While a de-escalation is withheld, `tier_held_by_recovery` is `True` and `instantaneous_tier` carries the un-held reading.
   - **Decision point — leaving the hold at $0$ makes the engine memoryless.** One transient bad tick then triggers an emergency flatten that is released on the very next tick. That is the flapping this skill exists to prevent.

5. **Emit and act on the report** — `penalty_breakdown` and `triggered_conditions` give the auditable reason the tier was assigned; log both. Route `cancel_resting_orders` to the venue-level cancel path and `flatten_positions` to an execution policy that does not assume the price feed is trustworthy.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Failing open on unevaluable telemetry.** Every penalty is guarded by a `>` comparison, and every `>` comparison against `NaN` is `False`. A staleness of `NaN` therefore accrues no penalty, scores $100$, and returns `ALLOW_FULL_TRADING` — the most permissive mandate produced by the least trustworthy input. The same holds for a negative staleness, which is what a clock-skewed feed produces when a tick is timestamped in the future.
- **Multiplying every order by `position_sizing_factor`.** At Tier 2 that factor is $0.0$, so a caller applying it uniformly silently suppresses the *exits* Tier 2 exists to permit — leaving the position on the book with no way to reduce it while the feed is degraded.
- **Rounding the score before classifying it.** A 2dp round applied ahead of the threshold comparison shifts up to $0.005$ of score across the boundary, always in the direction of more trading.
- **A memoryless emergency halt.** Tier 3 fires on one bad observation and clears on the next good one, so a flapping feed produces repeated cancel-and-flatten cycles, each of which pays the spread. Set `recovery_hold_seconds`.
- **Treating a crossed book as always a defect.** Locked and crossed quotations occur legitimately around auctions, halts and reopens; SRO rules under 17 CFR 242.610(e) carry explicit exceptions. A crossed-book check that runs through the pre-open will de-risk every morning for no reason — scope it to continuous trading.
- **Copying the default thresholds into production.** $90/70/40$ and the penalty weights are engineering placeholders, not standards. A feed whose normal staleness is $3\text{s}$ sits permanently at Tier 1 under the defaults.
- **Assuming an unmeasured check is a passed check.** `missing_sequence_count=0` and `crossed_book_detected=False` mean "measured, and clean". A collector that never computes a check leaves that penalty permanently at zero, and the score silently overstates quality.
- **Flattening at market on the feed you just distrusted.** Tier 3's mandate is to reduce exposure, not to cross a spread computed from prices the engine has declared unusable.

## Verification

- Instantiate `DataQualityDeRiskerEngine()`. Clean feed (`stale_time_seconds=0.1`) $\implies Q = 100.00$, Tier 0, `ALLOW_FULL_TRADING`, factor $1.0$.
- Boundary: `stale_time_seconds=2.0` $\implies$ penalty $10.0$, $Q = 90.00$, **Tier 0** (the bound is inclusive). `stale_time_seconds=2.5` $\implies$ penalty $15.0$, $Q = 85.00$, Tier 1, factor $0.50$.
- Crossed book alone $\implies$ penalty $50.0$, $Q = 50.00$, Tier 2: `allow_new_entries` false, `allow_risk_reducing_exits` true, `cancel_resting_orders` true, `flatten_positions` false.
- Crossed book with `stale_time_seconds=3.0` $\implies$ penalty $70.0$, $Q = 30.00$, Tier 3, `flatten_positions` true.
- Regression checks: `stale_time_seconds=2.0004` must give Tier 1, not Tier 0. `float("nan")`, `float("inf")`, a negative staleness, a non-positive spread multiplier and a negative `missing_sequence_count` must each give $Q = 0$, Tier 3 and `metrics_valid=False`.
- Recovery hold: with `recovery_hold_seconds=30.0` and an injected clock, a Tier 3 observation followed by clean observations stays at Tier 3 until $30.0\text{s}$ of sustained improvement, and a relapse restarts the timer.
- Run `python -m unittest discover -s skills/graduated-response-to-data-quality-degradation/scripts`.

## Related Skills

- `data-quality-monitoring-dashboard`
- `sequence-number-gap-detection-for-feeds`
- `capital-preservation-mode-for-degraded-conditions`
- `kill-switch-and-drawdown-circuit-breakers`
- `vendor-outage-fallback-data-source-hierarchy`
- `clock-skew-correction-for-tick-timestamps`
- `feed-handler-canary-deployment`
