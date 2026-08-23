# Deep Workflow Reference — cross-datacenter-clock-sync-validation

This file holds the full technical procedure referenced by `SKILL.md`. All thresholds named
below are configurable defaults, not published limits — see `references/standards.md`.

## Full Procedure

1. **Probe every region as one simultaneous snapshot.**
   - Per node, from `chronyc tracking` (or the `ptp4l` equivalent):
     - "System time … fast/slow of NTP time" → `reported_offset_ms` (signed, ms; positive
       = local clock ahead).
     - "Root delay" → `rtt_ms`. This is the node's sync-path delay to its reference clock,
       **not** the round-trip time of the monitoring query that fetched the probe.
     - "Root dispersion" → `root_dispersion_ms` (optional, defaults to 0.0).
     - The node's clock reading at the sampling instant → `timestamp_sec`.
   - Decision point: **do not evaluate a partial snapshot.** If a region fails to answer,
     pass what you have and let the module return `UNKNOWN` / denied. Silently dropping the
     unreachable region and validating the remainder is the exact failure this guards.
   - Decision point: the module cannot tell drift from sampling skew — both show up as a
     `timestamp_sec` difference. Set `max_sampling_skew_ms` to your collection budget so an
     implausibly large reading is annotated as probable skew in the veto message.

2. **Compute pairwise drift for every unordered pair.**
   - $\Delta \tau_{AB} = |(T_A - T_B) \cdot 1000 + (\text{offset}_A - \text{offset}_B)|$ ms.
   - Order of operations matters: difference the two epoch-magnitude readings *first*, then
     apply the offset difference in milliseconds. Folding a sub-millisecond offset into a
     $1.8 \times 10^{9}$ magnitude quantizes it at that magnitude's ULP (≈ 0.238 µs) before
     the subtraction can recover it.

3. **Attach the measurement uncertainty.**
   - $u_{AB} = \tfrac{1}{2}\text{rootdelay}_A + \tfrac{1}{2}\text{rootdelay}_B
     + \text{disp}_A + \text{disp}_B$.
   - Worst case reported as `max_worst_case_drift_ms` $= \Delta \tau_{AB} + u_{AB}$.
   - Uncertainty is **added**. chrony:
     `clock_error <= |offset| + root_dispersion + 0.5*root_delay`; RFC 5905 §4:
     `LAMBDA = EPSILON + DELTA/2`, "the maximum error due to all causes". Never subtract it.
   - Decision point: if $u_{AB} > \Delta \tau_{\text{max}}$, the measurement is too coarse to
     certify the limit. `is_measurement_conclusive` goes False and arbitration is denied
     irrespective of the point estimate. Fix the measurement path (local stratum-1, GPS, PTP
     grandmaster on site) rather than loosening the limit.

4. **Classify the worst pair and decide.**
   - `EXCELLENT` ≤ `excellent_drift_ms` → permitted.
   - `ACCEPTABLE` ≤ `max_allowed_drift_ms` → permitted.
   - `DEGRADED` ≤ `degraded_ceiling_ms` → **denied**, alert at elevated severity.
   - `BREACH` above that → **denied**, alert at top severity.
   - `UNKNOWN` (fewer than two usable probes) → **denied**.
   - `is_arbitration_allowed` = within limit **and** conclusive. Anything else falls back to
     single-region mode.

5. **Act on `vetoed_pairs`.**
   - The list names the specific region pairs past the limit, so a three-region deployment
     can isolate the one bad site rather than collapsing to a single region.

## Error handling

`ClockProbeError` (a `ValueError`) is raised — not returned — for probe data that cannot
yield a trustworthy verdict:

- any non-finite (`nan`, `inf`) field, including booleans and strings;
- an empty `region_id` or `datacenter_name`;
- a negative `rtt_ms` or `root_dispersion_ms` (would understate the uncertainty bound);
- duplicate `region_id`s across the probe set (would collapse pair keys and hide a pair);
- incoherent constructor thresholds (`max_allowed_drift_ms <= 0`, an `excellent_drift_ms`
  above the limit, a `degraded_ceiling_ms` below it).

Insufficient probes are the one non-raising failure: they return an `UNKNOWN` report with
arbitration denied, because a partially failed collection is an operational state the caller
must be able to log and alert on, not a programming error.

## Production Implementation Reference

- Reference code: `scripts/clock_sync_validator.py` (`CrossDatacenterClockSyncValidator`,
  `ClockSyncHealth`, `DatacenterClockProbe`, `CrossDatacenterSyncReport`,
  `ClockProbeError`).
- Automated unit tests: `scripts/test_clock_sync_validator.py`.
- Run: `python -m unittest discover -s skills/cross-datacenter-clock-sync-validation/scripts`
