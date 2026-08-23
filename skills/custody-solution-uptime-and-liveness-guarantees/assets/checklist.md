# Pre-Flight Checklist

## Contract and thresholds

- [ ] `target_uptime_pct` and `max_signing_latency_ms` are copied from the **executed provider agreement**, not from a marketing page or a default.
- [ ] The contract's **measurement window** for the uptime target is recorded, and the probe window audited matches it.
- [ ] It is understood that SOC 2 attests to the provider's *own* commitments and sets **no** uptime number.
- [ ] The provider's public status-page history has been reviewed alongside the contractual figure.

## Telemetry pipeline

- [ ] API health probes are active, logged at a known interval, and their interval is recorded.
- [ ] Probes carry a trustworthy `timestamp_ms`; clock skew between collectors is bounded.
- [ ] Probe volume over the audit window is sufficient — a p99 gate needs at least `min_latency_samples` (default 100) healthy samples.
- [ ] It is understood that uptime here is a **probe-success ratio**, not time-weighted availability, and that irregular probe spacing biases it.

## Fail-closed behaviour

- [ ] `max_probe_age_ms` is configured **and** `as_of_timestamp_ms` is supplied on every audit — freshness is otherwise not evaluated at all.
- [ ] The alerting path treats `UNKNOWN_NO_TELEMETRY` and `STALE_TELEMETRY` as incidents, not as missing data to be ignored.
- [ ] A dead-collector drill has been run: stop the probe pipeline and confirm the monitor reports failover rather than health.
- [ ] Malformed-telemetry `ValueError`s are surfaced to on-call, not swallowed by the caller.

## MPC quorum

- [ ] `mpc_threshold_k` and `mpc_total_n` match the provisioned cluster; construction rejects `k > n`.
- [ ] `QUORUM_AT_RISK` (`active == k`, zero remaining redundancy) is wired to a real alert, not just logged.
- [ ] Node maintenance windows are scheduled only while `redundant_nodes > 0`.
- [ ] MPC nodes are not co-located in a single region or provider (see `multi-party-computation-mpc-custody-solutions`).

## Failover

- [ ] `failover_on_latency_breach` has been set deliberately, with the trade-off between slow signing and a mid-volatility custody migration explicitly decided.
- [ ] `breached_conditions` — not just `status` — is surfaced to operators, so a quorum halt does not hide a concurrent uptime breach.
- [ ] The secondary custody provider is configured, funded, and its own liveness is monitored by a separate engine instance.
- [ ] Automated failover routing has been tested end-to-end under a simulated primary outage within the last quarter.
- [ ] Failover execution is separated from this engine's recommendation and is reviewable, since it moves capital.
