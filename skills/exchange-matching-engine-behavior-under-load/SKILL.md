---
name: exchange-matching-engine-behavior-under-load
description: >-
  Use when estimating how much extra latency a matching engine adds as inbound message
  rates approach its service capacity, and converting that engine utilisation into a
  market-making quoting directive (normal / widen spreads / pause passive quoting).
  Closed-form M/M/1 and M/D/1 queueing, explicit saturation handling, and the
  reject-and-disconnect behaviour real venues exhibit above a session throttle.
domain: Market Microstructure & High-Frequency Trading
subdomain: Order Book Queue Dynamics & Congestion
tags: ["matching-engine", "queuing-delay", "message-bursts", "adverse-selection", "queue-position", "mm1-queue", "md1-queue", "pollaczek-khinchine", "engine-saturation", "message-throttle", "latency-spikes"]
brokers_frameworks: ["CME Globex iLink 3", "Nasdaq INET / OUCH", "Eurex T7 ETI", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in high-frequency market making and microstructure simulation to answer one
question: *given the message rate the engine is currently absorbing, how much extra delay
should I assume before my cancel lands, and should I still be quoting?* During volatility
events (FOMC, CPI, index rebalances, crashes) inbound message rates at CME Globex, Nasdaq
INET and Eurex T7 spike by an order of magnitude. Because queueing delay grows as
$1/(1-\rho)$, an engine at $\rho = 0.9$ adds nine service times of delay where an engine at
$\rho = 0.5$ adds one. That delay lands on your **cancel** messages, which is what converts
a resting quote into a sniped quote.

## When NOT to Use

- **As a measurement.** This is a closed-form model, not telemetry. If you have real
  wire-to-wire timestamps, use them — see `tick-to-trade-latency-measurement` and
  `colocation-latency-budget-accounting`. This module is for the case where you can observe
  a *message rate* but not the engine's internal delay.
- **When $\lambda$ is a session average.** M/M/1 assumes Poisson arrivals. Real order flow
  is strongly clustered and self-exciting, so a model fed the daily mean rate materially
  understates burst delay. Feed the peak-window rate (see Pitfalls).
- **Above the venue's session throttle.** Past that point the venue does not queue your
  messages, it rejects them and eventually disconnects the session (CME, Eurex T7 and
  Nasdaq all behave this way — see `references/standards.md`). No queueing model describes
  that regime; you need reject handling, not a latency estimate. See
  `matching-engine-throttle-and-message-gapping-detection`.
- **At $\rho \ge 1$ as a latency number.** There is no steady state; the figure reported is
  a censored lower bound and is flagged `is_saturated`. Treat it as "unbounded", not "2 ms".
- **For queue *position* within a price level.** This models delay reaching the engine, not
  your rank once you are there — see `queue-position-modeling-for-passive-orders`.

## Prerequisites

- Mean per-message **service time** $\tau_s = 1/\mu$ in $\mu\text{s}$ — the reciprocal of the
  engine's sustainable rate, *not* a round-trip latency. Passed as `baseline_latency_us`.
- Engine (or market-segment partition) service rate $C = \mu$ in msgs/sec.
- Observed arrival rate $\lambda$ in msgs/sec, measured over a burst-scale window.
- Optionally, load-**independent** latency (network transit, serialisation, gateway hops)
  in $\mu\text{s}$, passed separately as `fixed_latency_us`.

## Units

All latencies are **microseconds**; all rates are **messages per second**. $\rho$ is
dimensionless. The two latency inputs are not interchangeable: `baseline_latency_us` is
multiplied by the congestion factor, `fixed_latency_us` is added to it.

## Workflow

1. **Measure $\lambda$ over a burst-scale window.**
   - **Decision point — window length sets the answer.** A 1-second window over an FOMC
     burst and a 1-hour session average differ by an order of magnitude and produce
     different directives from the same day. Use the shortest window your telemetry
     supports, and take the peak, not the mean.

2. **Decompose the latency budget before calling anything.**
   - $\tau_s = 1/\mu$ (engine service time, scaled by congestion) versus fixed transit
     (added). Passing a wire-to-wire round trip as $\tau_s$ inflates the result by the full
     $1/(1-\rho)$ factor — at $\rho = 0.9$ that is a 10x overstatement. The module computes
     `service_time_consistency_ratio` = $\tau_s \div (10^6/C)$ and warns when it leaves
     $[0.5, 2]$; **read that warning, do not filter it out.**

3. **Compute utilisation:** $\rho = \lambda / C$.

4. **Choose the service-time model.**
   - `M/M/1` (default) — exponential service, $W_q = \tau_s \rho/(1-\rho)$. Conservative.
   - `M/D/1` — deterministic service, $W_q = \tau_s \rho/(2(1-\rho))$, **exactly half**.
   - **Decision point.** A sequenced single-threaded matching engine has near-constant
     per-message cost, so M/D/1 is the better-specified model and M/M/1 is a ~2x safety
     margin on the queueing term. Pick one per venue and keep it fixed; do not switch
     mid-analysis and compare the outputs.

5. **Evaluate latency.**
   $$\text{Effective} = \tau_{\text{fixed}} + \tau_s + W_q, \qquad
     W_q^{M/M/1} = \tau_s\frac{\rho}{1-\rho}$$
   - **Decision point — check `effective_latency_is_lower_bound` before reading the latency
     figure.** It is set whenever $\rho$ exceeds the $0.99$ modelling cap, so the number
     reported is the value *at* the cap rather than an estimate. `is_saturated`
     ($\rho \ge 1$) is the stricter case: there is no steady state at all, and
     $\rho = 1.5$ and $\rho = 10$ return the same number. Escalate to reject/disconnect
     handling instead of quoting a latency.
   - Inputs are finite but the $1/(1-\rho)$ amplification can still overflow; the module
     raises rather than returning an infinite "latency".

6. **Emit the directive** (evaluated on exact $\rho$, non-strict lower bounds — a $\rho$
   landing *on* a threshold takes the more conservative branch):
   - $\rho < 0.50 \implies$ `NORMAL_OPERATIONS` (risk `LOW`)
   - $0.50 \le \rho < 0.85 \implies$ `WIDEN_PASSIVE_SPREADS` (risk `MODERATE`)
   - $\rho \ge 0.85 \implies$ `PAUSE_PASSIVE_QUOTING` (risk `HIGH_SNIPING_RISK`)
   - **Decision point — pausing does not flatten you.** `PAUSE_PASSIVE_QUOTING` stops *new*
     passive quotes. Quotes already resting are still exposed and their cancels are the
     messages now being delayed. Pair the directive with an explicit cancel/unwind path;
     `execution-algorithm-kill-switch-integration` owns the flatten decision.

7. **Record the `MatchingEngineLoadAuditReport`** — it is frozen, so it is safe to retain as
   the audit record of why quoting stopped.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Multiplying fixed latency by the congestion factor.** $1/(1-\rho)$ applies to the
  *service* term only. Folding 50 µs of network transit into $\tau_s$ at $\rho = 0.9$ turns
  a true 250 µs into a modelled 700 µs and pauses quoting on a fabricated number.
- **Feeding a session-average $\lambda$.** Order flow is clustered and self-exciting, not
  Poisson. Averaging a burst away puts $\rho$ in the `NORMAL_OPERATIONS` band during the
  exact minutes the engine is congested.
- **Treating a $\rho \ge 1$ latency as an estimate.** The queue has no steady state above
  $\rho = 1$; the reported figure is censored at the $0.99$ cap. Check `is_saturated`.
- **Assuming the engine queues your overflow.** It does not. CME rejects with a Business
  Level Reject (`35=j`) and terminates the session past a second threshold; Eurex T7 rejects
  on throttle breach and disconnects after a run of consecutive rejects; Nasdaq rejects
  above the port limit. A "high latency" model masks what is actually a **reject storm**.
- **Ignoring the queueing delay on cancels.** The adverse selection is not that your quote
  is stale — it is that your *cancel* is stuck behind the same aggressive sweep that made
  the quote stale. Delay is symmetric; the loss is not.
- **Conflating network transit with engine queue time.** Measuring RTT while ignoring the
  engine's inbound buffer attributes congestion to the wrong component and leads to buying
  a faster cross-connect that fixes nothing.
- **Assuming one engine.** Venue capacity is per market segment / partition, not
  venue-wide. Model the partition carrying your instrument, not the exchange.
- **Comparing M/M/1 and M/D/1 outputs as if they measured the same thing.** They differ by
  exactly 2x on the queueing term by construction, not because conditions changed.

## Verification

- Instantiate `ExchangeMatchingEngineLoadSimulator()` (defaults $0.85 / 0.50$, `M/M/1`) with
  $\tau_s = 20.0\,\mu\text{s}$, $C = 50{,}000$ msgs/sec (mutually consistent: $10^6/C = 20$).
  - $\lambda = 10{,}000 \Rightarrow \rho = 0.20$: `effective_latency_us` $= 25.0$,
    `queuing_delay_penalty_us` $= 5.0$, multiplier $1.25$, `NORMAL_OPERATIONS` / `LOW`.
  - $\lambda = 45{,}000 \Rightarrow \rho = 0.90$: `effective_latency_us` $= 200.0$
    ($10\times$), `queuing_delay_penalty_us` $= 180.0$, `PAUSE_PASSIVE_QUOTING` /
    `HIGH_SNIPING_RISK`.
- Same inputs with `service_model="M/D/1"` at $\rho = 0.90$: `queuing_delay_penalty_us`
  $= 90.0$ (exactly half) and `effective_latency_us` $= 110.0$, matching
  $\tau_s(2-\rho)/(2(1-\rho))$.
- Additivity: $\lambda = 45{,}000$ with `fixed_latency_us=50.0` gives $250.0\,\mu\text{s}$
  ($50 + 20 + 180$), **not** $700.0$, and `queuing_delay_penalty_us` stays $180.0$.
- Boundary: $\lambda = 42{,}500$ ($\rho = 0.85$ exactly) pauses; $\lambda = 42{,}499$
  ($\rho = 0.84998$) does **not** — the directive uses exact $\rho$, not the 4-dp reported
  value.
- Saturation: $\lambda = 75{,}000$ and $\lambda = 500{,}000$ both report `is_saturated`
  true, the same censored latency, and `utilization_factor_rho` of $1.5$ and $10.0$.
  $\lambda = 49{,}999.999$ ($\rho \approx 0.99999998$) reports `is_saturated` false but
  `effective_latency_is_lower_bound` true — the cap binds before saturation does.
- Negative checks — each must raise `ValueError`: a NaN or infinite capacity / arrival rate
  / service time / fixed latency; capacity $\le 0$; service time $\le 0$; a negative arrival
  rate or fixed latency; an empty `venue_id`; thresholds that are inverted, equal, $\le 0$
  or $> 1$; an unsupported `service_model`. A non-`EngineLoadMetrics` argument raises
  `TypeError`.
- Misuse guard: $\tau_s = 100\,\mu\text{s}$ against $C = 50{,}000$ logs a
  `SERVICE TIME INCONSISTENT` warning and reports `service_time_consistency_ratio` $= 5.0$.
- Run `python -m unittest test_exchange_matching_engine_behavior_under_load` from
  `scripts/` and confirm a 100% pass rate.

## Related Skills

- `matching-engine-throttle-and-message-gapping-detection`
- `message-rate-limit-vs-latency-tradeoff-tuning`
- `colocation-latency-budget-accounting`
- `queue-position-modeling-for-passive-orders`
- `latency-arbitrage-defensive-order-sizing`
- `post-only-limit-repricing-under-fast-markets`
- `adverse-selection-measurement-for-passive-orders`
- `microstructure-noise-filtering-for-hf-signals`
