# Standards for Cross-Region Data Replication Lag Monitoring

## Engineering standards enforced by this module

These are **self-imposed engineering budgets for this module**, not published
industry or regulatory standards. No regulator or exchange publishes a
cross-region replication-lag SLA. Calibrate the numbers against your own RPO,
strategy tolerance, and measured baseline before adopting them.

| Metric | Engineering Standard |
|---|---|
| Default P99 Replication Budget | Cross-region replication P99 lag SHOULD stay below $500\text{ ms}$; at or above that the replica is classified `UNSAFE_STALE`. Thresholds are inclusive (`>=`) so the boundary value escalates rather than passing. |
| Stale Read Isolation | Replicas classified `UNSAFE_STALE` MUST be isolated from execution read paths until lag recovers. The module only emits the verdict — the read path must honour it. |
| Fail-Safe Unknowns | A verdict of "healthy" MUST require positive evidence. Absent heartbeats (`UNKNOWN_NO_DATA`), too few samples for the claimed percentile (`UNKNOWN_INSUFFICIENT_SAMPLES`), and clock-skew-contaminated windows (`CLOCK_SKEW_SUSPECT`) all recommend read failover instead of reporting health. |
| Percentile Sample Floor | A P99 MUST be backed by at least $1/(1-0.99) = 100$ samples before it is treated as a tail statistic. With fewer samples, interpolation between the top two order statistics makes the reported "P99" effectively the observed maximum. |
| Sub-Percentile Spike Visibility | A P99 discards the worst 1% of samples by construction. Individual breaches of the unsafe threshold MUST therefore be counted and surfaced (`samples_over_unsafe_threshold`, `max_lag_ms`) rather than represented only through the tail statistic. |
| Continuous Heartbeat Probe | Heartbeat write probes SHOULD be issued at least once per second per region pair — the same cadence `pt-heartbeat` uses by default, and the rate at which a 100-sample P99 window spans ~100 s. |
| Clock Discipline | Both regions' hosts MUST be NTP/PTP-synchronised, and the measured sync bound MUST be configured as `clock_skew_tolerance_ms`. Negative measured lag MUST NOT be clamped to zero. |

## Measurement pattern — verified claims

The lag computed here is `replica_receive_timestamp − primary_write_timestamp`,
i.e. the replicated-heartbeat pattern. The two timestamps come from two different
host clocks, which is why clock synchronisation is a correctness prerequisite and
not a nicety.

| Claim | Source |
|---|---|
| The replicated-heartbeat pattern computes delay by reading the replicated record and computing "the difference from the current system time" on the replica, and "the clocks on the source and replica servers must be closely synchronized via NTP". Tool resolution is 0.01 s. | Percona Toolkit — `pt-heartbeat` documentation — https://docs.percona.com/percona-toolkit/pt-heartbeat.html |
| MirrorMaker 2's `replication-latency-ms` is "the difference between the system time at the moment of callback invocation and the timestamp of the original source record" — the same two-clock exposure — and "may be inaccurate under specific circumstances" (KAFKA-15068). | Apache Kafka — KIP-971 — https://cwiki.apache.org/confluence/display/KAFKA/KIP-971:+Expose+replication-record-lag+MirrorMaker2+metric |
| `AuroraGlobalDBReplicationLag`: "the average time elapsed replicating updates between the primary cluster's replication server and the secondary cluster's replication server", in **milliseconds**, available only in secondary Regions. `AuroraGlobalDBRPOLag` measures "how far the secondary cluster is behind the primary cluster for user transactions", also in milliseconds. | AWS — CloudWatch metrics for Amazon Aurora — https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/Aurora.AuroraMonitoring.Metrics.html |
| "After any write operation, Aurora replicates data to the secondary AWS Regions using dedicated infrastructure, with latency typically under a second." | AWS — Using Amazon Aurora Global Database — https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-global-database.html |
| Same-Region Aurora Replica lag is a different regime: "This lag is usually much less than 100 milliseconds after the primary instance has written an update. Replica lag varies depending on the rate of database change." | AWS — Replication with Amazon Aurora — https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/Aurora.Replication.html |

> **Scope note.** The AWS figures above are vendor-published typical behaviour
> (verified August 2026), not contractual guarantees, and they describe Aurora
> Global Database specifically — Kafka MirrorMaker 2, Redis/ElastiCache Global
> Datastore, and self-managed replication all behave differently. Measure your own
> baseline before setting thresholds. Nothing in this file is a regulatory
> requirement.
