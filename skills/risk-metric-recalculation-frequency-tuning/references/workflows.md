# Workflows for Risk Metric Recalculation Frequency Tuning

## 0. Prerequisite: separate what may be tiered from what may not

Before writing a single interval, split every risk computation in the engine
into two lists.

**Never tiered — evaluated per order, at order time:**
credit/capital threshold checks, position caps that gate order entry,
fat-finger price and size limits, duplicate-order detection, restricted-list
checks, and execution reporting to surveillance. 17 CFR 240.15c3-5(c)(1) frames
these as "prevent the entry of orders ... by rejecting orders"; a cadence
cannot express that.

**Tierable — analytics describing carried risk:**
portfolio VaR/CVaR, stress scenario grids, aggregate Greeks, exposure and
concentration reports, correlation matrices.

If a computation appears on both lists, it is really two computations: a cheap
per-order gate using the last published number, and an expensive periodic
refresh of that number. Tier only the refresh, and record how stale the gate's
input may be.

## 1. Classify metrics into cadence tiers

1. Measure the wall-clock cost of each tierable metric on production-sized data.
   Do not guess — the whole scheme is a cost/latency trade and you cannot make
   it without the cost side.
2. Assign a tier such that `accelerated_interval_sec` is comfortably longer than
   the measured cost. A 90 s stress test on a 30 s accelerated interval is
   permanently due and the tier is meaningless.
3. Tier 1 is for metrics that must run on every evaluation. Its intervals must be
   `0.0`; `RiskMetricScheduleConfig` raises on a non-zero tier-1 interval rather
   than silently ignoring it.
4. `accelerated_interval_sec` must not exceed `base_interval_sec`. Accelerating
   means calculating *more* often; the reversed config is rejected.
5. If any metric feeds a disorderly-trading alert and the firm is EU/UK
   authorised, its cadence is bounded by RTS 6 Article 16(5) — alerts within
   five seconds of the event. Put it in Tier 1 or Tier 2, or feed the alert from
   a separate always-on path.

## 2. Drive the scheduler

`evaluate_due_metrics(current_pnl_usd, current_timestamp_sec)` is the only entry
point. It is evaluation-driven: **nothing recalculates unless it is called.**

1. Call it from the market-data path on each tick (or each batch), and
   *additionally* from a wall-clock heartbeat at least as fast as your shortest
   non-tier-1 interval. Without the heartbeat, a stalled feed freezes every risk
   number with no signal that it has happened.
2. Pass timestamps from one non-decreasing clock — `time.monotonic()` live,
   event timestamps in replay. A decreasing timestamp raises.
3. Pass a finite P&L. `NaN` and `±inf` raise: a `NaN` compared against the
   velocity threshold is `False`, which would silently disable acceleration
   during exactly the feed corruption that warrants it.
4. Execute the metrics named in `metrics_due_for_calc`. The scheduler marks them
   calculated at the moment it returns them, so it assumes the caller runs them.
   If a metric fails, the scheduler has already advanced its clock — handle the
   failure in the caller and decide whether to force it due again.

## 3. Evaluate P&L velocity

```
velocity = |pnl_now - pnl_anchor| / (t_now - t_anchor)     when Δt >= min_velocity_sample_sec
         = previous velocity, anchor held                  otherwise
```

- The anchor is **held**, not reset, when the window is not met. The P&L change
  accumulated during those sub-window calls is therefore measured across the
  full span on the next qualifying call rather than being thrown away.
- The old `max(0.001, Δt)` clamp is not a safety guard but a noise amplifier: at
  a 1 ms tick gap it multiplies the P&L change by 1,000, and on a negative Δt
  from an out-of-order event it fabricates a velocity from nothing.
- Velocity is signless (absolute change). A violent rally invalidates stale risk
  numbers exactly as a crash does.

## 4. Switch cadence with hysteresis

| Transition | Condition |
|---|---|
| Normal → Accelerated | `velocity >= threshold` |
| Accelerated → Normal | `velocity <= threshold * acceleration_exit_ratio` **and** `dwell >= acceleration_min_dwell_sec` |

On entry to accelerated mode, every non-tier-1 metric is forced due on that same
call. This is the point of acceleration: without the force, a VaR calculated 1 s
ago is not due again for 4 s under a 5 s accelerated interval, so the detecting
tick logs an emergency and recomputes nothing.

Exiting is deliberately harder than entering. A single quiet sample mid-crash
must not return stress testing to a 300 s cadence, so both a lower velocity
*and* a minimum dwell are required. Set `acceleration_exit_ratio=1.0` and
`acceleration_min_dwell_sec=0.0` only if you explicitly want the old stateless
per-sample behaviour.

## 5. Audit the schedule

Each report carries:

| Field | Meaning |
|---|---|
| `metrics_due_for_calc` | Metrics the caller must now execute, in schedule order |
| `is_accelerated_mode` | Cadence mode after this evaluation |
| `pnl_velocity_usd_per_sec` | Velocity estimate in force (rounded for display) |
| `overdue_metrics` | Cadences missed by more than `staleness_multiple ×` the interval that was in force *before* this call |
| `calculation_load_reduction_pct` | Cumulative scheduled load vs a recompute-everything baseline |
| `evaluations_observed` / `cost_units_executed` / `cost_units_naive` | Raw counters behind the reduction figure |
| `status_message` | Human-readable line, also emitted to the module logger |

Operational reading:

- `overdue_metrics` non-empty once, after a known gap: the feed or heartbeat
  stopped. Investigate the driver.
- `overdue_metrics` non-empty persistently: the evaluation rate is below what
  the tier demands. Either drive the tuner faster or widen the interval — the
  cadence as configured is fiction, and an unmet cadence is not something you
  can evidence in an annual review.
- `calculation_load_reduction_pct` is cumulative since construction, so it lags
  a recent change in tick rate. It is an invocation ratio unless you supplied
  measured `relative_cost_units`, and it is never a CPU-cycle figure.

## 6. Calibration loop

1. Run a full session in shadow with the default schedule.
2. Record the reduction figure, the accelerated-mode duty cycle, and every
   `overdue_metrics` occurrence.
3. If accelerated mode was engaged for a large fraction of the session, the
   velocity threshold is too low for the book — raise it, do not shorten the
   base intervals.
4. If accelerated mode never engaged during a session that contained a genuine
   volatility event, the threshold is too high.
5. Re-measure metric costs whenever the position count or scenario grid grows;
   a tier that was comfortable at 200 positions may be permanently due at 2,000.
6. Record the final parameters alongside the risk-control documentation. Under
   § 15c3-5(e)(1) they fall inside the annual effectiveness review.
