# Workflows for Network Jitter Impact on Strategy Performance

## 1. Capture the delay series correctly (upstream of this module)

- A one-way delay spans **two hosts**, so a monotonic clock cannot measure it. You need
  synchronised real-time clocks, and the error bar on every delay is their combined
  divergence from UTC. Under MiFID II RTS 25 an HFT firm's business clocks may each sit
  100 µs from UTC, so a two-clock measurement can carry 200 µs of error.
- Prefer NIC hardware timestamps (`SO_TIMESTAMPING`) over user-space `recv()` return
  times: a software timestamp includes kernel queueing and scheduler delay, which is
  jitter you are attributing to the network. See
  `network-interface-level-tick-timestamping` and
  `hardware-timestamping-vs-software-timestamping-accuracy`.
- Size the window to the tail you intend to audit: **100 packets for P99**. Below that
  the nearest rank for P99 lands on the last element and the "P99" is the maximum.
- Capture from **one** tap. Merging two taps of the same traffic duplicates observations
  and biases every percentile; the engine warns on duplicate `packet_id`s for this
  reason.
- Record what the strategy actually depends on. Auditing a heartbeat flow and applying
  the verdict to a market-data path assumes the two share a queue; they usually do not.

## 2. Validate the series

`analyze_jitter_impact` rejects, rather than repairs:

| Input | Why it is rejected outright |
|---|---|
| Empty | Nothing to audit. |
| One packet | σ is zero by construction; the window would read as a perfectly jitter-free link. |
| NaN / Inf timestamp | Breaks `sorted()` silently; `NaN <= budget` is `False` for every budget, so a corrupted capture reads as a *pass*. |
| Negative delay | Receive before send proves the two clocks disagree; the positive delays share an unknown error. |
| Non-numeric / bool timestamp | `True` would otherwise be read as 1 ns. |
| Delay > 1e12 ms | A unit error — usually a raw nanosecond duration passed where a nanosecond timestamp was expected. |

Do not filter the negatives and audit the remainder. The whole window is suspect.

## 3. Compute the delay distribution

Nearest rank, `ceil(p/100 × N)` into the ascending-sorted series, with HdrHistogram's
one-ULP nudge on the percentile:

```
delays (ms):  50 × 1.0 , 50 × 9.0        (N = 100)

  nearest rank P50 = ceil(0.50 × 100) = 50   -> 50th ascending value = 1.0 ms   correct
  v1.0.0 index     = int(100 × 0.50)  = 50   -> 51st ascending value = 9.0 ms   wrong
```

The same off-by-one made the v1.0.0 P99 over 100 packets the observed maximum. If you
have inherited dashboards built on the old numbers, expect every percentile to move
down by one rank after this change.

## 4. Read the three variation metrics against each other

| Observation | Reading | Remediation |
|---|---|---|
| σ high, IQR high | The whole distribution is wide. | Genuine link/queueing variability — path, buffer sizing, contention. |
| σ high, IQR ≈ 0 | The body is tight and something stalls. | Find the stalling component (GC pause, hypervisor steal, interrupt coalescing, a burst refill). Re-tuning the link will not help. |
| σ ≫ PDV P99 | The stall is **rarer than 1 in 100**, so it sits above P99 entirely and the tail figure does not see it. σ does, because σ is not robust. | Audit a rarer percentile. A 1-in-200 stall needs P99.5 or P99.9 and at least 1,000 packets — use `latency-monitoring-percentile-based-slas`, which resolves P99.9 and corrects for coordinated omission. Do not conclude the link is fine because P99 is clean. |
| σ low, PDV P99 high relative to min | Rare packets fall far behind a path that is demonstrably capable of better. | Tail-only damage; this is the adverse-selection channel. |
| PDV P99 ≈ 0 | Nothing in the audited window fell behind the best case. | Check the window is long enough to contain an excursion at all. |

PDV is `P99 − min` per RFC 5481, anchored on the minimum because the minimum is *proof*
of what the path can do. σ is what the Sharpe model consumes; IQR is the robust body
measure that does not move for one stall.

## 5. Audit the budgets

Three independent checks, each contributing a named string to `report.breaches`:

```
SHARPE_BELOW_FLOOR        base_sharpe - gamma * sigma  <  target_sharpe_min
JITTER_STD_OVER_CEILING   sigma                        >  max_acceptable_jitter_ms
P99_LATENCY_OVER_BUDGET   p99                          >  max_p99_latency_ms   (if set)
```

- All comparisons run on **unrounded** values; rounding is applied to the report fields
  only. A P99 of 5.0004 ms against a 5 ms budget is a breach.
- The Sharpe check uses the **unclamped** modelled value. The reported
  `simulated_degraded_sharpe` is clamped to `max(0.0, ·)` for presentation, and a
  configuration with a negative `target_sharpe_min` would otherwise read that clamped
  0.0 as passing.
- `JITTER_STD_OVER_CEILING` is deliberately independent of the Sharpe model. A γ small
  enough to imply a 150 ms tolerance must not be able to approve a link you have
  separately decided is too variable. (In v1.0.0 `max_acceptable_jitter_ms` was declared
  but never read, so setting it had no effect at all.)

## 6. Apply the breach/approval asymmetry

| Verdict | Meaning | Sample requirement |
|---|---|---|
| `JITTER_HIGH_RISK_WARNING` | An over-budget value was observed. | Any count ≥ 2. Ten packets are enough to observe a breach. |
| `JITTER_HEALTHY` | Nothing breached **and** P99 was resolvable. | ≥ 100 packets. |
| `JITTER_INSUFFICIENT_SAMPLES` | Nothing breached, but the window cannot resolve P99. | — |

`JITTER_INSUFFICIENT_SAMPLES` means *not measured*, not *within budget*, and
`is_jitter_acceptable` is `False` for it. The fix is a longer capture.

## 7. Fit gamma before trusting any Sharpe figure

γ has units of **Sharpe lost per millisecond of delay σ** and nobody publishes it. To
obtain one:

1. Partition live trading into comparable windows (same instrument, venue, session
   phase, and broadly similar volatility — otherwise you are fitting regime, not jitter).
2. For each window, compute realized Sharpe and the delay σ over the same interval.
3. Regress Sharpe on σ. The negated slope is γ; record the range of σ the fit covers.
4. Re-fit whenever the strategy, venue, instrument or network path changes.

Outside the fitted σ range the linear form is an extrapolation, and the published
cost-of-latency relationship is concave in the delay (Moallemi & Saglam 2013), so the
straight line will bend away from the truth in a direction the fit cannot tell you.
Record `report.sharpe_model` alongside the number so downstream consumers can tell a
model output from a measurement.

## 8. Act on the verdict

A breach is a statement about the link, not an instruction. Common responses, roughly in
order of cost:

- Move the measurement closer to the wire (hardware timestamps) and confirm the jitter
  is on the network rather than in the capture path.
- Remove host-side contention: CPU pinning and NUMA locality, interrupt coalescing off,
  no shared cores with noisy neighbours (`feed-handler-cpu-pinning-and-numa-awareness`,
  `cross-strategy-shared-infrastructure-resource-contention`).
- Bypass the kernel network stack (Onload, DPDK) so scheduler and softirq delay stop
  contributing to the tail.
- Shorten or re-path the link — colocation, a different cross-connect, microwave versus
  fibre for a cross-market hop.
- Change the strategy's exposure to the tail: widen quotes, size down, or move to order
  types less exposed to being picked off (`latency-arbitrage-defensive-order-sizing`,
  `post-only-limit-repricing-under-fast-markets`).

Halting trading on a live excursion is **not** this module's job — it is a windowed,
after-the-fact audit. Put that in a dedicated risk control
(`risk-control-latency-budget`, `kill-switch-and-drawdown-circuit-breakers`).
