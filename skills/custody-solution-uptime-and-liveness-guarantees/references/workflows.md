# Workflows for Custody Solution Uptime and Liveness Guarantees

The governing principle throughout: **a liveness monitor must fail closed.** Every
step below is written so that the inability to establish liveness produces a
non-healthy result, never a healthy one.

## 1. Configure from the contract

```python
config = ProviderSlaConfig(
    provider_id="FIREBLOCKS_01",
    provider_name="Fireblocks Institutional Custody",
    target_uptime_pct=99.9,          # from the executed agreement
    max_signing_latency_ms=2000.0,   # from the executed agreement
    mpc_threshold_k=2,
    mpc_total_n=3,
    max_probe_age_ms=60_000.0,       # freshness bound
    min_latency_samples=100,         # below this, p99 is reported but not gated
    latency_rolling_window=None,     # or an int to cap the latency sample
    failover_on_latency_breach=False,
)
engine = CustodyLivenessMonitorEngine(config)
```

Construction validates the cluster and the thresholds. `k > n` raises — a 4-of-3
quorum can never be satisfied, and that should surface at startup rather than at the
first signing attempt. `target_uptime_pct` outside `[0, 100]`, non-positive latency
or freshness bounds, and non-finite values all raise here too.

## 2. Ingest probes

Probes are validated on every audit and rejected — not quarantined — when malformed:

| Condition | Why it raises |
| :--- | :--- |
| non-finite `signing_latency_ms` / `timestamp_ms` | `NaN > threshold` is False, so NaN silently *passes* the SLA gate |
| negative `signing_latency_ms` | physically impossible; indicates a clock or parsing fault |
| negative `active_mpc_nodes` | ditto |
| `active_mpc_nodes > mpc_total_n` | the feed disagrees with the provisioned cluster; trusting it can mask a real quorum loss |

Do **not** pre-sort and do not rely on list order. `audit_liveness` sorts by
`timestamp_ms` itself, because concurrent collectors routinely deliver an older
probe last and reading the final element as "now" is precisely how a quorum loss
goes unseen.

## 3. Evaluate uptime

$$\text{Uptime \%} = \frac{N_{\text{healthy}}}{N_{\text{total}}} \times 100$$

The value is rounded once, then both reported and compared on that same rounded
value. Rounding to display precision *before* the comparison is a fail-open bug: a
true 99.896% rounds to 99.90% and clears a 99.9% target it actually missed.

Remember this is a probe-success ratio, not time-weighted availability. It matches
the contractual measure only when probes are evenly spaced; `probe_count`,
`window_start_ms`, and `window_end_ms` are reported so you can verify that.

## 4. Evaluate MPC quorum

```
redundant_nodes = active_nodes - mpc_threshold_k
```

| Condition | Status | Failover |
| :--- | :--- | :--- |
| `redundant_nodes > 0` | healthy | no |
| `redundant_nodes == 0` | `QUORUM_AT_RISK` | no — warning only |
| `redundant_nodes < 0` | `QUORUM_LOST_LIVENESS_HALT` | yes |

The middle row is the one worth internalising. In a 2-of-3 cluster, two active nodes
still signs — but any maintenance on either remaining node halts signing entirely.
Alerting only once quorum is already lost gives operators no time to act.

## 5. Evaluate P99 signing latency

Computed over healthy probes only (an unhealthy probe has no meaningful signing
time), optionally capped to the most recent `latency_rolling_window` samples.

The gate is conditional on sample count. When
`latency_sample_count < min_latency_samples`, `percentiles_reliable` is False, the
P99 is still reported, and a breach is logged as **NOT GATED** rather than counted:
a 99th percentile cannot resolve the top 1% of fewer than 100 observations, so below
that it is simply the maximum and a single slow request reads as an SLA breach.

## 6. Evaluate freshness

Pass `as_of_timestamp_ms`. It is a parameter rather than a clock read so audits stay
deterministic and replayable against recorded telemetry.

- omitted → freshness is **not evaluated**, and the report says so in
  `recommendations` rather than implying the data is current.
- `newest_probe_age_ms > max_probe_age_ms` → `STALE_TELEMETRY`, failover recommended.

## 7. Combine and act

Conditions are evaluated independently and accumulated. `status` is the most severe;
`breached_conditions` holds all of them. Severity, worst last:

```
HEALTHY < QUORUM_AT_RISK < DEGRADED_SLA_BREACH < STALE_TELEMETRY
        < UNKNOWN_NO_TELEMETRY < QUORUM_LOST_LIVENESS_HALT
```

Failover is recommended for quorum loss, uptime breach, stale telemetry, and no
telemetry. A latency breach recommends failover only when
`failover_on_latency_breach=True` — slow signing is usually preferable to a custody
migration during a volatility spike, and that trade should be chosen explicitly.

This module **recommends**; it does not execute. Routing the secondary provider is
the caller's job — see `broker-failover-secondary-account-routing` — and should stay
separately reviewable, because failover moves capital.
