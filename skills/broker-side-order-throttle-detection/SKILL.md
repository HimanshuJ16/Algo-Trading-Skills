---
name: broker-side-order-throttle-detection
description: Use when a bot's broker does not signal congestion explicitly, to detect
  undeclared broker-side order throttling from acknowledgment round-trip latency (ACK
  RTT) using an exponentially weighted mean and variance baseline plus an acknowledgment
  timeout sweep, and to pace order dispatch with AIMD backoff.
domain: algorithmic-trading
subdomain: broker-integration
tags:
- broker-integration
- order-throttle
- latency-monitoring
- ack-rtt
- silent-throttling
- aimd-backoff
- ewma-anomaly-detection
brokers_frameworks:
- Interactive Brokers TWS API
- Interactive Brokers Web API
- Binance Spot REST API
- Python High-Frequency Engine
version: "3.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when a bot dispatches order flow to a venue that **queues or paces
excess messages instead of rejecting them**, so congestion never appears as an error
code and shows up only as acknowledgment latency.

The Interactive Brokers TWS API is the reference case: it "is designed to accept up to
fifty messages per second coming from the client side," and beyond that rate messages
are queued and delayed rather than refused. The `+PACEAPI` connect option makes TWS
pace the client at 50/s instead of disconnecting it. In neither case does the client
receive a signal — the only observable is ACK RTT rising.

Use it to establish a latency baseline that a sustained throttle cannot quietly move,
to detect acknowledgments that never arrive at all, and to slow dispatch until the
condition clears.

## When NOT to Use

- **When the venue tells you.** Binance returns HTTP 429 on a rate-limit breach and
  HTTP 418 once an IP is auto-banned for continuing to send after 429s, both carrying a
  `Retry-After` header; the IBKR *Web* API returns 429 and may put the IP in a 10-minute
  penalty box. An explicit response is authoritative and a latency inference is not —
  obey `Retry-After` and do not let this detector shorten it. See
  `multi-broker-rate-limit-handling`.
- **As a pre-trade message limit.** MiFID II RTS 6 Article 15(1)(d) requires "maximum
  messages limits, which prevent sending an excessive number of messages to order books
  pertaining to the submission, modification or cancellation of an order." That is a
  hard counter against a known limit, enforced before dispatch. A latency-derived
  backoff is not a substitute; see
  `matching-engine-throttle-and-message-gapping-detection`.
- **On order flow too sparse to build a baseline.** Roughly 20+ acknowledgments are
  needed before the anomaly test means anything. Below that the skill reports `WARMUP`
  and only the absolute ceiling and the ACK timeout can fire.
- **To attribute the delay.** Rising ACK RTT is equally consistent with a local GC
  pause, a saturated NIC, a congested uplink or a venue-side matching-engine slowdown.
  The skill establishes that dispatch should slow down, not who caused it.

## Prerequisites

- Submission and acknowledgment timestamps taken from the **same monotonic clock**
  (`time.monotonic()`), in one process. Wall-clock timestamps can step backwards under
  NTP correction and produce negative round trips.
- A measured ACK RTT distribution for your own deployment, to calibrate
  `max_absolute_rtt_ms`. The 500 ms default is a placeholder, not a standard.
- A dispatch loop that can actually honour a backoff, and a separate path for
  risk-critical cancels that must never be delayed by it.

## Workflow

1. **Register every submission, not just every acknowledgment.**
   Call `register_order_submission(order_id, t_sub)` at dispatch. A detector fed only by
   completed ACKs sees only the orders that were *not* throttled into silence — the
   sample stream is survivorship-biased, and the worst throttle produces no sample at all.

2. **Record acknowledgments and compute RTT.**
   On ACK, call `record_order_ack(order_id, t_sub, t_ack)`; RTT is `(t_ack - t_sub) x 1000` ms.
   A non-finite or time-reversed timestamp raises `ThrottleDataError` — do not clamp it
   to 0 ms. A fabricated 0 ms sample pulls the baseline down and makes the next healthy
   acknowledgments look anomalous.

3. **Maintain the baseline, excluding throttled samples.**
   Update the exponentially weighted mean and variance (Finch 2009, eq. 143) only from
   samples not classified as throttled. Admitting the anomaly is what lets a sustained
   throttle train the baseline onto itself and go quiet while still in force.

4. **Classify against the pre-update baseline.**
   - `RTT >= max_absolute_rtt_ms` → `SILENT_THROTTLE`, regardless of warmup.
   - baseline warm and `z >= z_score_threshold` → `SILENT_THROTTLE`.
   - baseline not yet warm → `WARMUP` (report it; do not report it as healthy).
   - `z >= elevated_z_threshold` → `ELEVATED_LATENCY`.
   - otherwise → `NORMAL`.

   where `z = (RTT - EWMA) / sqrt(max(EWMVar, min_variance_clamp))`. Evaluate every
   threshold against the baseline *as it stood before this sample*, so the reported mean,
   deviation and z-score reconcile and the decision is auditable.

5. **Sweep for acknowledgments that never came.**
   Call `sweep_pending_acks(now)` at least as often as `ack_timeout_ms`. Any order older
   than the timeout is reported `ACK_TIMEOUT` — the most severe state — once, then
   dropped from the pending table so repeated sweeps do not re-escalate the same stall.

6. **Apply AIMD backoff.**
   On a congestion signal, decrease dispatch rate multiplicatively (multiply the delay by
   `backoff_multiplier`, clamped to `max_backoff_ms`). On a healthy acknowledgment,
   increase dispatch rate additively (subtract `backoff_additive_decrease_ms`, to a floor
   of zero). Chiu & Jain (1989) is the control law being applied.

7. **Decide what recovery means, explicitly.**
   By default the baseline stays frozen for as long as throttling persists, so a
   sustained throttle keeps alarming and keeps the backoff at its ceiling until a human
   intervenes. If the latency shift is genuinely permanent (a re-route, a venue
   migration), set `rebaseline_after_consecutive` to re-anchor after N consecutive
   throttled samples — accepting that the detector will then go quiet at the new level.

> Full procedure: see `references/workflows.md`.
> Parameter reference and sourcing: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Letting the throttle train its own baseline.** If throttled samples are folded into
  the EWMA, a persistent 300 ms throttle against a 15 ms baseline reads `NORMAL` within
  about four samples and the backoff decays to zero while the broker is still queuing
  every order. Sustained throttling is the normal case during a volatility event, not
  the exotic one — exclude throttled samples from the baseline.
- **Measuring only the orders that came back.** The worst silent throttle is an
  acknowledgment that never arrives. It generates no RTT sample, so a detector without a
  pending-order timeout keeps reporting the last healthy state indefinitely.
- **Clamping a negative round trip to zero.** `max(0.0, t_ack - t_sub)` turns a
  backwards clock step or an out-of-order callback into a "perfect" 0 ms acknowledgment
  that drags the baseline down. Under the same clamp a NaN timestamp also becomes 0 ms,
  because `max(0.0, nan)` returns `0.0` — the detector is then quietly mis-calibrated
  with nothing logged. Reject both.
- **Trusting a z-score computed on a one-sample baseline.** The first acknowledgment
  initialises the mean to itself with zero variance. If it landed inside a throttle, the
  poisoned baseline is the reference for the whole session. Report `WARMUP` until enough
  samples have accumulated, while keeping the absolute ceiling live.
- **Confusing network jitter with broker throttling.** An isolated packet delay is not
  systemic congestion. The variance floor plus a warmup requirement is what separates them.
- **Overriding an explicit `Retry-After`.** A latency inference is weaker evidence than
  the venue's own answer. When both are present the venue wins.
- **Backing off risk-critical cancels.** The recommended delay is for new order flow.
  Applying it to a kill-switch cancel means the backoff has become the risk.

## Verification

- Establish a 15 ms baseline, then feed a *sustained* 300 ms RTT (above baseline, below
  the 500 ms ceiling) and confirm every sample stays `SILENT_THROTTLE`, the backoff
  climbs to `max_backoff_ms`, and the baseline mean is unchanged at 15 ms.
- Feed a single 600 ms spike during warmup and confirm `SILENT_THROTTLE` still fires.
- Register an order, never acknowledge it, sweep past `ack_timeout_ms`, and confirm one
  `ACK_TIMEOUT` report and no second report on the next sweep.
- Confirm a NaN timestamp and a `t_ack < t_sub` pair each raise `ThrottleDataError` and
  leave the baseline untouched.
- Confirm `z_score` in the report equals `(latest_rtt_ms - ewma_rtt_ms) / ewmsd_rtt_ms`.
- Drive concurrent acknowledgments from multiple threads and confirm no samples are lost.
- Run `python -m unittest discover -s skills/broker-side-order-throttle-detection/scripts` and confirm a 100% pass rate.

## Related Skills

- `multi-broker-rate-limit-handling`
- `matching-engine-throttle-and-message-gapping-detection`
- `tick-buffering-burst-handling`
- `structured-logging-for-post-incident-forensics`
