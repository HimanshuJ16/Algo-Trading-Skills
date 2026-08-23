# Pre-Flight / Sign-off Checklist — cross-datacenter-clock-sync-validation

Use this before considering the skill's implementation complete.

## Scope

- [ ] **Right question:** Confirm this is being used for *pairwise* inter-region agreement,
      and that per-host divergence from UTC is covered separately by
      `clock-drift-monitoring-alerting-thresholds`. Pairwise agreement proves nothing about
      UTC accuracy — two clocks can agree perfectly and both be 10 ms out.
- [ ] **Not claimed as RTS 25 compliance:** Confirm no document states that passing this
      validator evidences MiFID II RTS 25 compliance (Article 4 requires a documented,
      annually reviewed traceability system).

## Probe collection

- [ ] **Simultaneous snapshot:** Confirm all regions are probed as one coordinated snapshot;
      probes gathered 500 ms apart read as 500 ms of drift.
- [ ] **Sampling-skew budget:** Confirm `max_sampling_skew_ms` is set to the collection
      budget (it is 0.0 / disabled by default).
- [ ] **Field semantics:** Confirm `rtt_ms` carries the node's sync-path **root delay**
      (`chronyc tracking` "Root delay"), not the monitoring query's round-trip time.
- [ ] **Dispersion:** Confirm `root_dispersion_ms` is populated where the daemon reports it.
- [ ] **No partial snapshots:** Confirm a failed regional probe is passed through rather than
      dropped, so the module can return `UNKNOWN` / denied instead of validating a subset.

## Calculation

- [ ] **Uncertainty adds:** Confirm no local variant subtracts RTT/2 from measured drift
      (chrony: `clock_error <= |offset| + root_dispersion + 0.5*root_delay`).
- [ ] **Conclusiveness gate:** Confirm `is_measurement_conclusive` is honoured — a pair whose
      combined uncertainty exceeds the limit cannot evidence it, whatever the point estimate.
- [ ] **Resolution floor:** Confirm the target limit is comfortably above `RESOLUTION_FLOOR_MS`
      (~0.24 µs); sub-microsecond agreement cannot be evidenced through `timestamp_sec`.
- [ ] **Unique regions:** Confirm `region_id` is unique per probe.

## Thresholds and enforcement

- [ ] **Threshold sourced from activity:** Confirm `max_allowed_drift_ms` comes from the
      binding ceiling and jurisdiction, not the shipped 1.0 ms default
      (`MIFID_HFT_IMPLIED_PAIRWISE_BUDGET_MS` = 200 µs for an EU HFT book; FINRA Rule 6820
      is 50 ms for a US CAT reporter).
- [ ] **DEGRADED denies:** Confirm operators and runbooks understand `DEGRADED` is already
      vetoed — it is a severity label, not permission to continue.
- [ ] **Fail-closed paths:** Confirm `UNKNOWN`, non-conclusive and breach verdicts all route
      to single-region fallback.
- [ ] **Veto is wired:** Confirm `is_arbitration_allowed` actually gates the arbitration path;
      this module reports, it does not halt.
- [ ] **Pair isolation:** Confirm `vetoed_pairs` is used to isolate the offending site rather
      than collapsing every region.
- [ ] **Probe errors handled:** Confirm `ClockProbeError` is caught and alerted, not swallowed.

## Testing

- [ ] **Automated testing:** Run
      `python -m unittest discover -s skills/cross-datacenter-clock-sync-validation/scripts`
      — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
