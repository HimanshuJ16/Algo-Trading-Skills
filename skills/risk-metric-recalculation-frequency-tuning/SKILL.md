---
name: risk-metric-recalculation-frequency-tuning
description: >-
  Use when a real-time risk engine cannot recompute every metric on every tick; assigns
  metrics to cadence tiers and accelerates them under stress. Never use it to defer a
  per-order pre-trade check.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: risk-management
  tags: risk-management, risk-frequency, recalculation-cadence, volatility-acceleration, pnl-velocity, tiered-scheduling
  brokers_frameworks: "Risk Metric Scheduler; Python Standard Library"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when a real-time risk engine is CPU-bound or latency-bound and you need to decide *how often* each risk metric is recomputed. Recalculating a 10,000-path Monte Carlo VaR, a full option-Greeks aggregation, and a portfolio stress grid on every incoming tick starves the thread that is supposed to be watching drawdown — the cheap control that actually stops losses gets delayed by the expensive analytics that merely describe them.

`RiskMetricFrequencyTuner` assigns each metric a cadence tier, answers "which metrics are due at this instant", shortens every cadence while P&L is moving fast, and reports the cadences it demonstrably failed to meet.

## When NOT to Use

- **Never to gate a per-order pre-trade control.** 17 CFR 240.15c3-5(c)(1)(i) requires controls reasonably designed to "[p]revent the entry of orders that exceed appropriate pre-set credit or capital thresholds ... by rejecting orders if such orders would exceed the applicable credit or capital thresholds". That is a property of *every order*, not of a cadence. Credit checks, capital checks, position caps, fat-finger price/size limits and duplicate-order checks are evaluated per order, at order time, always. Putting any of them on a 30-second tier is a rule breach, not an optimisation. Tier only the analytics: portfolio VaR, stress scenarios, aggregate Greeks. See `sec-rule-15c3-5-risk-controls-us`.
- **Not for a metric that feeds a regulatory real-time alert in the EU/UK.** MiFID II RTS 6 (Commission Delegated Regulation (EU) 2017/589) Article 16(5): "Real-time alerts shall be generated within five seconds after the relevant event." A disorderly-trading alert derived from a 30 s or 300 s tier cannot meet that bound. See `mifid-ii-algo-trading-compliance-eu`.
- **Not as the only trigger for risk recalculation.** Dueness is evaluation-driven: nothing is recomputed unless `evaluate_due_metrics` is called. A stalled feed stops the tick stream *and* the risk engine. Drive the tuner from a wall-clock heartbeat as well as from ticks.
- **Not as a substitute for a kill switch.** Recalculating faster does not stop trading. The breach action belongs to `kill-switch-and-drawdown-circuit-breakers`.
- **Not when the tier interval is shorter than the metric's own compute time.** A 90-second stress test cannot run on a 30-second accelerated cadence; the scheduler will simply report it due on every call. Measure your metric costs first — see `risk-control-latency-budget`.

## Prerequisites

- A list of `RiskMetricScheduleConfig` entries (`metric_name`, `tier`, `base_interval_sec`, `accelerated_interval_sec`, optional `relative_cost_units`), or the built-in `default_schedule()`.
- A **single, non-decreasing clock**. `time.monotonic()` for a live engine, event timestamps for a replay harness. Mixing clocks, or replaying events out of order, raises rather than silently producing a fabricated velocity.
- A P&L series in one currency, marked consistently. The tuner takes a scalar; a multi-currency book must be converted before the call (`multi-currency-pnl-and-fx-conversion`).
- A P&L velocity threshold in $/sec that you are willing to defend. The default of $500/s is a house number sized for a small single book, not a standard.
- Measured per-metric compute costs if you intend to report load reduction as anything other than an invocation count.

## Workflow

1. **Classify metrics into cadence tiers — and decide first what must *not* be tiered.**
   - Tier 1 (`TICK_DRAWDOWN`): every evaluation. Intervals must be `0.0`; a non-zero interval on a tier-1 metric raises, because the scheduler would otherwise ignore it and the config would be a lie.
   - Tier 2 (`GREEKS_DELTA`): 2.0 s base / 0.5 s accelerated.
   - Tier 3 (`VAR_1DAY`): 30.0 s base / 5.0 s accelerated.
   - Tier 4 (`STRESS_TEST`): 300.0 s base / 30.0 s accelerated.
   - **Decision point — mandatory per-order controls never enter this table.** If a metric answers "may this specific order be sent", it is not a tiered metric. If it answers "how much risk are we carrying", it is.

2. **Measure P&L velocity over a window, not between adjacent ticks.**
   - $\text{Velocity} = |\Delta \text{PnL}| / \Delta t$, but only once $\Delta t \ge$ `min_velocity_sample_sec` (default 0.25 s).
   - **Decision point — below the window, hold the anchor instead of dividing.** At a 100 µs tick gap a $1 P&L wobble divides out to $10,000/s and accelerates the whole engine on noise. Holding the anchor (rather than resetting it) means the change accumulated during sub-window calls is measured across the full span once the window is met, not discarded.
   - **Decision point — a non-finite P&L raises.** `NaN >= threshold` is `False`, so a corrupted feed would silently pin the engine in *normal* cadence at exactly the moment its numbers are least trustworthy. Reject the input instead.

3. **Switch cadence with hysteresis, not per-sample.**
   - Enter accelerated mode when velocity $\ge$ threshold.
   - **Decision point — exit requires two conditions, not one.** Velocity must fall to $\le$ `threshold × acceleration_exit_ratio` (default 0.5) *and* `acceleration_min_dwell_sec` (default 30 s) must have elapsed. One quiet sample in the middle of a crash must not drop stress testing back to a 300-second cadence.
   - **Decision point — the tick that detects the spike recomputes immediately.** On entry, every non-tier-1 metric is forced due. Without this, a VaR last run 1 s ago is not due again for another 4 s under a 5 s accelerated interval: the engine announces an emergency and then waits it out.

4. **Report what was actually scheduled, and what was missed.**
   - `overdue_metrics` lists metrics whose gap since last calculation exceeded `staleness_multiple ×` the interval that was **in force before this call** — so a mode change is not misreported as a missed cadence.
   - **Decision point — a persistently overdue metric means the cadence is fiction.** Either the feed stalled, or the evaluation rate is lower than the tier demands. Fix the driver or widen the tier; do not ignore the flag.
   - `calculation_load_reduction_pct` is computed from the invocations this instance actually scheduled versus a recompute-everything baseline. It is **not** a CPU measurement: with the default `relative_cost_units=1.0` all metrics count equally, which understates the real saving. Supply measured costs to weight it, and never quote it as a benchmark.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Tiering a control that the market access rule requires per order.** The saving looks the same in a CPU profile and is a regulatory breach. Credit, capital, price, size and duplicate-order checks are per-order, always.
- **Recalculating heavy metrics on every tick.** A 10,000-path Monte Carlo VaR per tick freezes the pre-trade risk thread — the failure this skill exists to prevent, and the reason tiering is worth doing at all.
- **Leaving cadences static during a crash.** Stress tests on a 300 s cadence mean the first useful post-crash number arrives up to five minutes after the move.
- **Announcing acceleration without recomputing.** If entering accelerated mode does not force the heavy metrics due, the detecting tick logs a warning and changes nothing until the shortened interval happens to elapse.
- **Letting the mode flap.** A stateless per-sample comparison flips between a 300 s and a 30 s stress cadence tick by tick during a volatile session, and the engine spends its budget on mode changes.
- **Dividing by a near-zero tick gap.** `max(0.001, dt)` looks like a safe guard and is a velocity amplifier: at a 1 ms gap it multiplies the P&L change by 1,000.
- **Treating an out-of-order timestamp as a small positive gap.** A replayed event that arrives late produces a negative `dt`; clamping it manufactures a six-figure velocity and a spurious acceleration.
- **Assuming a tick-driven scheduler keeps running when the feed dies.** It does not. The risk numbers freeze at their last values and nothing says so unless staleness is reported.
- **Quoting a fabricated CPU saving.** "Saves 75% of CPU cycles" is not measurable from a schedule alone — metric costs differ by orders of magnitude, so an invocation count is not a cycle count.
- **Assuming P&L velocity detects all risk.** Gamma, correlation and concentration can build in a flat-P&L market. Velocity is a proxy for "risk numbers are going stale fast", not a risk measure.

## Verification

- Instantiate `RiskMetricFrequencyTuner()` and evaluate at `t=100.0`: all four default metrics are due (none has ever been calculated). Evaluate again at `t=101.0` with a $10 move: only `TICK_DRAWDOWN` is due, and `is_accelerated_mode` is `False`.
- Clock-epoch independence: the first evaluation of a fresh tuner must return the same due list at `t=1_700_000_000.0` and at `t=0.5`. A `0.0` "never calculated" sentinel fails this.
- Boundary: with `GREEKS_DELTA` last run at `t=100.0`, it is not due at `t=101.9` and is due at exactly `t=102.0`. With a $500/s threshold, exactly $500/s accelerates and $499.99/s does not.
- Acceleration: a $2,000 P&L drop over 1.0 s sets `is_accelerated_mode=True` **and** returns all four metrics due on that same call, including `STRESS_TEST`.
- Hysteresis: after entry, a quiet sample 1 s later must stay accelerated; at $300/s with dwell satisfied it must stay accelerated (above the $250/s exit level); quiet with dwell satisfied must exit.
- Velocity windowing: a $1 change over 1 ms reports $0.00/s, not $1,000/s. A $300 change at 0.1 s followed by $300 more at 0.5 s reports $1,200/s — accumulated across the held anchor, not discarded.
- Negative checks: `NaN`/`±inf` P&L, `NaN` timestamp, a backwards timestamp, an `accelerated_interval_sec` above `base_interval_sec`, a non-zero interval on a tier-1 metric, duplicate metric names, and each out-of-range constructor argument must all raise `ValueError`.
- Staleness: after a 70 s gap, `GREEKS_DELTA` and `VAR_1DAY` appear in `overdue_metrics` and `STRESS_TEST` does not; the status message contains `RISK METRICS STALE`.
- Load reduction: 10 evaluations at 1 s spacing on the default schedule execute 17 of 40 possible invocations = 57.50%. Confirm a single tier-1 metric reports 0.00%, never the old hard-coded 75.0.
- Run `python -m unittest discover -s skills/risk-metric-recalculation-frequency-tuning/scripts` and confirm 100% pass rate.

## Related Skills

- `risk-control-latency-budget`
- `sec-rule-15c3-5-risk-controls-us`
- `mifid-ii-algo-trading-compliance-eu`
- `value-at-risk-var-live-monitoring`
- `real-time-greeks-recalculation-on-market-moves`
- `kill-switch-and-drawdown-circuit-breakers`
- `risk-limit-calibration-against-historical-drawdowns`
