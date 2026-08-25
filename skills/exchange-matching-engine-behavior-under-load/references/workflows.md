# Workflows for Exchange Matching Engine Behavior Under Load

All latencies are microseconds; all rates are messages per second.

## 1. Establish the engine's parameters

1. **Service rate $C = \mu$.** Use the sustainable throughput of the **market segment /
   partition** carrying your instrument, not a venue-wide headline figure. Venue capacity
   is partitioned; so is congestion.
2. **Service time $\tau_s = 10^6/C\ \mu\text{s}$.** This is the quantity the congestion
   factor multiplies. Derive it from $C$; do not substitute an observed round trip.
3. **Fixed latency $\tau_{\text{fixed}}$.** Everything load-independent: cross-connect,
   switch hops, wire serialisation, gateway processing that does not queue. Supplied
   separately as `fixed_latency_us` and only ever added.
4. **Record where each number came from.** A capacity figure inferred from a vendor blog is
   an assumption, not a measurement, and the whole output inherits its uncertainty.

## 2. Measure the arrival rate

1. Count inbound messages on the partition over a **burst-scale** window (sub-second to a
   few seconds). Market data message counts are the usual proxy for aggregate load.
2. Take the peak of that window, not the session mean. Averaging a burst away is the
   commonest way to get a `NORMAL_OPERATIONS` directive during a congested minute.
3. Distinguish **aggregate** load from **your session's** rate. $\rho$ is about the engine;
   your own throttle budget is a separate constraint (Section 5).

## 3. Evaluate the model

1. Build `EngineLoadMetrics(venue_id, baseline_latency_us=τ_s,
   engine_capacity_msgs_per_sec=C, arrival_rate_msgs_per_sec=λ, fixed_latency_us=τ_fixed)`.
   Construction validates the inputs and **raises** on anything non-finite, non-positive or
   negative — catch it per observation, count it as a quarantined sample, and continue.
   A rejected sample is a telemetry defect, not a quiet engine.
2. Call `simulate_matching_engine_load(metrics)`. This is a closed-form evaluation, not a
   Monte Carlo run; it is deterministic and allocates one report.
3. Check `service_time_consistency_ratio` on the report. Far from 1.0 means $\tau_s$ and $C$
   do not describe the same server and the multiplied term is wrong.
4. Check `effective_latency_is_lower_bound` **before** reading `effective_latency_us`. It is
   set whenever $\rho$ exceeds the $0.99$ modelling cap, so the latency is censored at that
   cap and comparisons between two censored readings are meaningless. `is_saturated`
   ($\rho \ge 1$) is the stricter condition and additionally means there is no steady state.

## 4. Act on the directive

| Directive | Action |
|---|---|
| `NORMAL_OPERATIONS` | Quote normally. Keep sampling — $\rho$ can cross both bands within one burst. |
| `WIDEN_PASSIVE_SPREADS` | Widen quotes by enough to cover the expected mark-out over the added delay $W_q$, not by a fixed tick count. The delay is a number the report gives you; use it. |
| `PAUSE_PASSIVE_QUOTING` | Stop submitting new passive quotes **and** work the resting inventory down. Pausing new quotes alone leaves the existing book exposed with delayed cancels — the exact risk the directive is responding to. |

Hysteresis: apply a dwell time or a lower re-entry threshold before resuming. A $\rho$
oscillating around 0.85 will otherwise flap the directive and generate its own message
storm — which raises $\rho$ further.

## 5. Handle the regime the model does not cover

Above the venue's session throttle the engine rejects rather than queues.

1. Instrument reject rates and reject reason codes alongside $\rho$. A rising reject count
   with a *falling* apparent latency means you are being throttled, not that congestion
   eased.
2. Track consecutive rejects against the venue's disconnect limit. Eurex T7 disconnects
   after a documented run of consecutive throttle rejects; CME terminates the iLink session
   past its Terminate threshold.
3. Have a session re-establishment and state-resynchronisation path ready — after a
   disconnect your view of resting orders is stale, and re-quoting before reconciling risks
   duplicate exposure.
4. Never respond to a reject with an immediate unbounded retry. That converts a throttle
   breach into a disconnect.

## 6. Calibrate and re-validate

1. Back-test the directive bands against your own fill and mark-out data: what was the
   realised adverse selection on fills taken at each $\rho$ decile? Move the thresholds to
   where the mark-outs actually turn, rather than keeping 0.50/0.85 by default.
2. Compare M/M/1 against M/D/1 on the same history. If realised delay tracks the M/D/1
   curve, the M/M/1 default is costing you quoting time in the moderate band.
3. Re-derive $C$ whenever the venue changes platform release or repartitions segments.
