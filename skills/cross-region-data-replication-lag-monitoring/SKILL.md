---
name: cross-region-data-replication-lag-monitoring
description: Quantitative observability module for measuring cross-region database
  and message broker (Aurora, Kafka, Redis) replication lag, tracking P95/P99 SLAs,
  and triggering stale-read failovers.
domain: Infrastructure & Real-Time Architecture
subdomain: Cross-Region Telemetry
tags:
- cross-region
- data-replication
- replication-lag
- p99-sla
- stale-reads
- aurora
- kafka-mirrormaker
brokers_frameworks:
- Kafka
- Aurora
- Python Dataclasses
version: "1.1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in distributed quantitative trading architectures operating multi-region databases or event streams (e.g., AWS Aurora Global Database, Kafka MirrorMaker 2, Redis Cross-Region Replication). Streaming market data, trade fills, or account balance updates across regions (e.g., `us-east-1` to `eu-west-1`) incurs network replication latency. If secondary replicas lag behind primary writers, trading algorithms reading from stale secondary nodes risk executing duplicate orders or using outdated position states. This module computes P95/P99 replication lag from heartbeat samples and classifies whether the replica is safe to serve reads.

## When NOT to Use

- **You need enforcement, not classification.** This module returns a verdict (`is_read_failover_recommended`). It does not talk to a database, a router, or a connection pool, and it cannot stop a bot from reading a stale replica. Wire the verdict into whatever actually owns read routing.
- **Your two regions do not share a synchronised clock.** The lag here is `replica_clock_now − primary_clock_write_time`; without NTP/PTP discipline across both hosts the number is an unknown clock offset plus an unknown lag. The module detects the obvious case (negative lags) and refuses to certify health, but it cannot correct a positive skew that inflates or deflates the measurement. See `cross-datacenter-clock-sync-validation`.
- **You want the engine's own lag metric instead of an end-to-end one.** Aurora publishes `AuroraGlobalDBReplicationLag` and `AuroraGlobalDBRPOLag` (both in milliseconds, both computed inside AWS); MirrorMaker 2 publishes `replication-latency-ms`. Those need no clock-sync assumption on your side. Use this module when you need one uniform, application-visible measurement across heterogeneous stores (Aurora + Kafka + Redis), or when you need to measure the *readable* lag your application actually sees rather than the engine's internal replication lag.
- **You are measuring intra-region replica lag.** The default 100 ms / 500 ms bands are sized for cross-region links. AWS documents same-Region Aurora Replica lag as usually much less than 100 ms, versus cross-Region global-database latency typically under a second — different regimes, so reusing these bands intra-region would be meaningless. Retune the thresholds.
- **You need to detect a *stopped* replica from lag alone.** A replica that stops applying changes may also stop emitting heartbeats. That shows up here as `UNKNOWN_NO_DATA` (fail-safe), not as a large lag — treat absent heartbeats as an outage, and pair this with liveness monitoring.

## Prerequisites

- Cross-region heartbeat records carrying `primary_write_timestamp_ms` (stamped by the primary's clock) and `replica_receive_timestamp_ms` (stamped by the replica's clock), in epoch milliseconds.
- NTP/PTP synchronisation verified across both regions' hosts, and a **known** sync bound to configure as `clock_skew_tolerance_ms`.
- A heartbeat window large enough for the percentile you claim: a P99 needs at least 100 samples to be an observed order statistic (`min_sample_count`, default 100). At the standard once-per-second probe rate, that is a ≥100 s window.
- Maximum allowable replication lag SLA (e.g. P99 $\le 500\text{ ms}$) chosen from *your* RPO and strategy tolerance — see `references/standards.md` on why 500 ms is a default, not a law.
- **The caller windows the data.** `evaluate_replica_health` filters by region pair but does not sort, deduplicate, or truncate. Passing a day of heartbeats to a "rolling 5-minute window" silently widens it.

## Workflow

1. **Heartbeat Ingestion**:
   - Ingest heartbeat payload for region pair ($R_{\text{primary}} \to R_{\text{replica}}$). Non-finite (NaN/Inf) timestamps are rejected with `ValueError` — a NaN would propagate through the percentiles and make every threshold comparison False, silently reporting a stale replica as `HEALTHY`.
2. **Replication Lag Calculation**:
   - Calculate signed latency: $\Delta t = t_{\text{replica}} - t_{\text{primary}}$. Negative values are **kept, not clamped**: they are evidence of clock skew, not of a zero-lag replica.
3. **P95 / P99 Metric Computation**:
   - Compute P95 and P99 (linear interpolation between order statistics) across the rolling sample window $N$ the caller supplied.
4. **Trust Gates Before Health Classification** — each of these fails safe (`is_read_failover_recommended = True`) rather than reporting health:
   - No heartbeats for the pair $\implies$ `UNKNOWN_NO_DATA`. A silent probe is a fault signal, never a clean bill of health.
   - Any lag below $-\texttt{clock\_skew\_tolerance\_ms}$ $\implies$ `CLOCK_SKEW_SUSPECT`. The whole window is biased by an unknown offset, so the P99 cannot be trusted in either direction.
   - $N <$ `min_sample_count` (default 100) $\implies$ `UNKNOWN_INSUFFICIENT_SAMPLES`. Below 100 samples the "P99" is really the observed maximum.
5. **Replica Health Classification** (thresholds are inclusive — `>=` — so the boundary value escalates; classification uses the unrounded P99, while `p99_lag_ms` in the report is rounded to 2 dp, so trust `status`, not a comparison you re-derive from the rounded field):
   - If $\text{P99} \ge 500\text{ ms} \implies$ `UNSAFE_STALE` (block reads / fail over to primary). This is evaluated **first**, so an observed unsafe lag escalates even in a short or skewed window.
   - Else if skew/sample-count gates tripped $\implies$ the corresponding `CLOCK_SKEW_SUSPECT` / `UNKNOWN_INSUFFICIENT_SAMPLES` verdict above.
   - Else if $\text{P99} \ge 100\text{ ms} \implies$ `DEGRADED_WARNING` (alert; reads still permitted).
   - Else $\implies$ `HEALTHY`.
   - Independently of the P99 verdict, `samples_over_unsafe_threshold` counts individual heartbeats at or above the unsafe threshold, and `max_lag_ms` reports the worst one. A P99 discards the worst 1% by construction, so two 1.8 s stalls in a 600-sample window leave it `HEALTHY` — decide separately whether your strategy tolerates those windows.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Reading Stale Balances**: Allowing trading bots in secondary regions to read un-replicated position balances during replication spikes, causing over-leveraging.
- **Relying on Mean Replication Lag**: Evaluating average lag instead of P99 tail latency, missing intermittent multi-second replication spikes that a mean over a large window barely moves.
- **Un-synchronized Host Clocks**: Measuring cross-region replication lag without validating PTP/NTP synchronisation. This is not a footnote — the measurement *is* a difference of two clocks. Percona's `pt-heartbeat`, which uses exactly this pattern, states the requirement outright (see `references/standards.md`), and MirrorMaker 2's `replication-latency-ms` has the same exposure.
- **Clamping Negative Lag to Zero**: `max(0, lag)` turns the clearest available symptom of clock skew — a replica timestamp earlier than the primary's — into a perfect health score. A replica whose clock runs 200 ms slow then reports 200 ms *less* lag than reality on every sample, right up to the point where the numbers go negative and get clamped away.
- **Claiming a P99 From a Handful of Samples**: with 5 heartbeats, `numpy.percentile(lags, 99)` interpolates between the top two values and returns roughly the maximum. It will not show you a 1-in-100 spike, because you have not observed 100 events.
- **Treating Silence as Health**: an empty heartbeat window means the probe, the link, or the replica is down. Any monitor that returns `HEALTHY` for zero samples will certify a dead region.
- **Treating a Healthy P99 as "No Stale Reads Happened"**: a P99 ignores the worst 1% of samples by definition. Two multi-second replication stalls in a 10-minute, once-per-second window are 0.33% of samples and leave the P99 untouched, yet each one is a real window in which a bot read stale positions. Watch `samples_over_unsafe_threshold` and `max_lag_ms` alongside the tail statistic.
- **Reusing These Thresholds Everywhere**: 100 ms / 500 ms are cross-region defaults in this module, not a published standard. Same-region replicas, and strategies with a tighter RPO, need their own numbers.

## Verification

- Instantiate `CrossRegionReplicationLagMonitor`. Input 100 heartbeat records for `us-east-1` $\to$ `eu-west-1` with lags cycling 40–44 ms. Verify status is `HEALTHY` with P95 = P99 = 44.0 ms and mean = 42.0 ms. Inject 5 delayed heartbeats of $1200\text{ ms}$ and verify the monitor flags `UNSAFE_STALE` with `is_read_failover_recommended = True`.
- Feed 100 heartbeats whose lag is exactly $500\text{ ms}$ and verify `UNSAFE_STALE` (thresholds are inclusive).
- Feed 100 heartbeats with a $-10\text{ ms}$ lag and verify `CLOCK_SKEW_SUSPECT` with failover recommended — not `HEALTHY`.
- Evaluate a region pair with no heartbeats and verify `UNKNOWN_NO_DATA` with failover recommended.
- Feed 5 fast heartbeats and verify `UNKNOWN_INSUFFICIENT_SAMPLES`; then feed 4 fast heartbeats plus one $3000\text{ ms}$ sample and verify the observed unsafe lag still escalates to `UNSAFE_STALE`.
- Set one timestamp to `NaN` and verify `ValueError` is raised rather than a `HEALTHY` verdict.
- Feed 598 heartbeats at $40	ext{ ms}$ plus two at $1800	ext{ ms}$; verify status stays `HEALTHY` (P99 unmoved) while `samples_over_unsafe_threshold` is 2 and `max_lag_ms` is 1800.0.
- Run `python -m unittest discover -s skills/cross-region-data-replication-lag-monitoring/scripts`.

## Related Skills

- `cross-datacenter-clock-sync-validation`
- `disaster-recovery-runbook-for-full-region-outage`
- `cost-monitoring-for-cloud-trading-infrastructure`
---
