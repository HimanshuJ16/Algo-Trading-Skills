---
name: custody-solution-uptime-and-liveness-guarantees
description: Fail-closed custody liveness monitor tracking API uptime against a contractual
  SLA target, MPC signing quorum (k-of-n) with early warning at zero redundancy, and
  sample-gated P99 signing latency, recommending failover on breach, stale telemetry,
  or no telemetry.
domain: Crypto Custody & Security
subdomain: Custody SLA & Liveness
tags:
- custody-sla
- liveness-guarantees
- mpc-quorum
- signing-latency
- fireblocks
- bitgo
- anchorage
- uptime-monitoring
brokers_frameworks:
- SOC 2 Type II
- Python Dataclasses
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill on institutional crypto desks whose automated strategies depend on a custody provider being able to *sign right now*. In 24/7 crypto markets a custody outage or an MPC quorum failure during a volatility spike blocks margin top-ups and liquidation defence, and the loss is realized before a human notices.

The engine consumes health-probe telemetry and answers one question — is the primary custodian live enough to sign, and if not, should we fail over? It reports:

- **Uptime** against your *contractual* target, compared without a rounding step that could mask a breach.
- **MPC quorum** for a k-of-n signing cluster, including a `QUORUM_AT_RISK` warning at zero remaining redundancy — before the halt, not during it.
- **P99 signing latency**, gated on having enough samples for a 99th percentile to mean anything.

**It fails closed.** No telemetry, stale telemetry, and malformed telemetry are all non-healthy outcomes that recommend failover. Silence is not health.

## When NOT to Use

- **As the failover executor.** This engine sets `is_failover_recommended`. It does not move keys, re-route signing, or touch the secondary provider. Failover is a capital-moving action; keep the decision and the execution separate and human-reviewable.
- **As a source of SLA numbers.** Every threshold is an input, not a constant. `target_uptime_pct` and `max_signing_latency_ms` come from your executed agreement with the provider. No numeric SLA is hard-coded or implied here.
- **As a time-weighted availability calculator for SLA credits.** Uptime is computed as a probe-success ratio, which equals contractual availability only when probes are evenly spaced. Claims for service credits should be computed from the provider's own incident record and your contract's measurement method.
- **As a substitute for the provider's status page.** A probe pipeline can be healthy while the custodian is degraded in ways your probes do not exercise (for example, withdrawals halted while reads succeed).

## Prerequisites

- **Contractual SLA terms** from the executed provider agreement: `target_uptime_pct`, `max_signing_latency_ms`, and the measurement window they are defined over.
- **Cluster shape**: `mpc_threshold_k` (shares required to sign) and `mpc_total_n` (shares provisioned). A `k`-of-`n` cluster tolerates exactly `n - k` unavailable nodes.
- **Continuous health-probe telemetry** carrying `timestamp_ms`, `is_api_healthy`, `signing_latency_ms`, and `active_mpc_nodes`.
- **A freshness bound** (`max_probe_age_ms`) and a wall-clock reference (`as_of_timestamp_ms`) if staleness is to be detected at all.

## Workflow

1. **Configure from the contract, then validate.** `CustodyLivenessMonitorEngine(config)` rejects an unsatisfiable cluster (`k > n`), a `target_uptime_pct` outside `[0, 100]`, and non-positive latency or freshness bounds at construction. A 4-of-3 quorum is dead on arrival and should fail at startup, not at the first signing attempt.
2. **Feed probes; do not pre-sort them.** `audit_liveness` orders by `timestamp_ms` itself. Do not rely on list order to identify the current state — concurrent collectors routinely deliver an older probe last, and taking the final list element as "now" is how a real quorum loss goes unseen.
3. **Pass `as_of_timestamp_ms` whenever you have a clock.** Without it, freshness is *not evaluated* and the report says so explicitly. With it, a newest probe older than `max_probe_age_ms` yields `STALE_TELEMETRY` and recommends failover — a frozen collector must not read as a healthy custodian.
4. **Read `breached_conditions`, not just `status`.** `status` is the single most severe condition; `breached_conditions` and `recommendations` carry all of them. A quorum halt and an uptime breach commonly occur together and the operator needs both.
5. **Check `percentiles_reliable` before acting on P99.** When `latency_sample_count < min_latency_samples` the P99 is reported but explicitly not gated, because a 99th percentile drawn from a handful of samples is the maximum wearing a percentile's name. Widen the window rather than lowering the bar.
6. **Decide failover deliberately.** Quorum loss, uptime breach, stale telemetry, and no telemetry all set `is_failover_recommended`. A latency breach does so only when `failover_on_latency_breach=True`, because slow signing is usually better than a custody migration mid-volatility — make that trade explicit rather than inheriting it.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating "no data" as "no problem".** An empty probe list is the signature of a dead collector, not a perfect custodian. Any monitor that returns healthy when blind will report healthy through the exact incident it exists to catch.
- **Rounding before comparing.** Rounding uptime to two decimals and then testing it against a 99.9% target lets a true 99.896% clear the gate. Round for display, compare on the raw value.
- **Trusting list order for "current" state.** `probes[-1]` is the last element, not the newest observation. Sort by timestamp before reading the current node count.
- **Letting NaN through.** Every `>` comparison against NaN is False, so one corrupt latency reading passes an SLA gate instead of tripping it. Reject non-finite telemetry at ingestion.
- **Alerting only after quorum is lost.** In a 2-of-3 cluster, two active nodes is not healthy — it is one node from a signing halt, and routine maintenance on either node causes it. Warn at zero remaining redundancy.
- **Gating on an under-sampled P99.** A 99th percentile needs at least 100 observations to resolve the top 1% at all; below that it is just the maximum and one unlucky request looks like an SLA breach.
- **Reporting one breach at a time.** An `if/elif` chain hides concurrent failures; the quorum alarm masks the uptime breach that explains it.
- **Reading a 99.9% figure as a standard.** SOC 2 does not set an uptime number — see `references/standards.md`. The number is contractual, and it is only meaningful alongside its measurement window.
- **Confusing probe-ratio uptime with time-weighted availability.** If probe frequency rises during incidents (retries, denser polling), the ratio is biased relative to the contractual measure.

## Verification

- Construct `ProviderSlaConfig(target_uptime_pct=99.9, max_signing_latency_ms=2000.0, mpc_threshold_k=2, mpc_total_n=3)`. Feed 100 healthy probes at 450 ms with 3 active nodes → `HEALTHY`, `redundant_nodes == 1`, no failover.
- Feed 24,974 healthy probes and 26 unhealthy (99.896%) → `DEGRADED_SLA_BREACH` with failover. Rounding to two decimals would have shown 99.9% and passed.
- Feed probes where the newest by timestamp reports 1 active node but arrives mid-list → `QUORUM_LOST_LIVENESS_HALT`, `current_active_mpc_nodes == 1`.
- Feed an empty list → `UNKNOWN_NO_TELEMETRY` with `is_failover_recommended is True`.
- Set `mpc_threshold_k=4, mpc_total_n=3` → `ValueError` at construction.

```bash
python -m unittest discover -s skills/custody-solution-uptime-and-liveness-guarantees/scripts
```

## Related Skills

- `custodial-vs-non-custodial-tradeoff-assessment`
- `multi-party-computation-mpc-custody-solutions`
- `custody-solution-vendor-due-diligence-checklist`
- `broker-failover-secondary-account-routing`
