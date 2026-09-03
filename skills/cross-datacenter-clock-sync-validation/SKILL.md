---
name: cross-datacenter-clock-sync-validation
description: >-
  Use when a strategy merges ticks from more than one datacenter (NY4, LD4, a cloud
  region) and must prove those sites agree closely enough to order events correctly.
  Measures pairwise inter-region drift with its uncertainty and fails closed.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: real-time-architecture
  tags: real-time-architecture, clock-sync, cross-datacenter, ptp, ntp, clock-drift, multi-region
  brokers_frameworks: "Clock Sync Validator; Python Real-Time Engine"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when a strategy or arbitration module consumes ticks from **more than one
datacenter** and orders them against each other — Chicago CME against Equinix NY4, NY4
against LD4, one cloud region against another. If the sites' clocks disagree by more than
the tick-arrival spread, cross-region ordering inverts ($t_{\text{LD4}} < t_{\text{NY4}}$
for an event that genuinely happened later), and the strategy sees phantom latency
arbitrage and a corrupted book.

`CrossDatacenterClockSyncValidator` takes a simultaneous snapshot of per-node clock
telemetry, computes drift for every unordered pair, carries the measurement uncertainty
alongside the point estimate, and returns a **fail-closed** verdict in
`is_arbitration_allowed`.

## When NOT to Use

- **As a measure of divergence from UTC.** This measures sites against *each other*. Two
  clocks that agree perfectly can both be 10 ms from UTC. Per-host UTC divergence and the
  latched halt that must follow a breach are `clock-drift-monitoring-alerting-thresholds`.
- **As evidence of MiFID II RTS 25 compliance.** RTS 25 bounds each business clock against
  UTC, not pairs against each other, and Article 4 additionally requires a documented,
  annually reviewed traceability system. See `references/standards.md` for how the two
  quantities relate and why neither implies the other.
- **To configure the sync stack.** `ptp4l`, `phc2sys`, NIC hardware timestamping and
  grandmaster selection belong to `clock-synchronization-ptp-for-trading-hosts`. Do not
  build a second, divergent set of thresholds here.
- **As the enforcement point.** This returns a verdict; it does not halt trading. Wire
  `is_arbitration_allowed` into the arbitration gate yourself.
- **Below about 1 µs.** `timestamp_sec` is a float at Unix-epoch magnitude, whose ULP is
  ~0.24 µs (`RESOLUTION_FLOOR_MS`). Sub-microsecond agreement cannot be evidenced through
  that field — carry fine offsets in `reported_offset_ms`, which stays small and precise.
- **On probes gathered at different times.** The module cannot distinguish clock drift from
  probe sampling skew; both appear as a `timestamp_sec` difference.

## Prerequisites

- Nodes running an NTP or PTP daemon (`chrony`, `linuxptp`) with queryable telemetry.
- A **coordinated simultaneous snapshot** of all regions. Probes gathered 500 ms apart read
  as 500 ms of drift. Set `max_sampling_skew_ms` to your collection budget so an implausible
  reading is annotated as probable skew rather than reported as clock failure.
- A pairwise drift limit chosen from your activity and jurisdiction, not from the default.
  See `references/standards.md`; `MIFID_HFT_IMPLIED_PAIRWISE_BUDGET_MS` (200 µs) is the
  budget implied by two RTS 25 HFT-compliant clocks.
- Per node: sync-path **root delay** and (optionally) **root dispersion** — `chronyc
  tracking` "Root delay" / "Root dispersion". `rtt_ms` is that root delay, *not* the
  round-trip time of the monitoring query.

## Workflow

1. **Snapshot every region's clock telemetry at one instant.**
   - `chronyc tracking` (System time → `reported_offset_ms`, Root delay → `rtt_ms`, Root
     dispersion → `root_dispersion_ms`) or the equivalent `ptp4l` fields.
   - Decision point: if a region's probe fails, do **not** drop it and evaluate the rest.
     Fewer than two probes returns `UNKNOWN` with arbitration denied, and that is the
     intended outcome — a failed remote probe must never read as "one healthy region,
     proceed".

2. **Compute pairwise drift and its uncertainty** for every unordered pair:
   - Point estimate:
     $\Delta \tau_{AB} = |(T_A - T_B) \cdot 1000 + (\text{offset}_A - \text{offset}_B)|$ ms.
     The epoch-magnitude readings are differenced *first*; folding a sub-millisecond offset
     into a $1.8 \times 10^{9}$ magnitude quantizes it away before the subtraction.
   - Uncertainty (added, never subtracted):
     $u_{AB} = \tfrac{1}{2}(\text{rootdelay}_A + \text{rootdelay}_B) + \text{disp}_A + \text{disp}_B$.
   - Decision point: if $u_{AB} > \Delta \tau_{\text{max}}$ the measurement cannot evidence
     the limit at all — `is_measurement_conclusive` is False and arbitration is denied
     however small the point estimate is.

3. **Classify the worst pair** against the configured tiers (defaults shown; all three are
   constructor arguments because no published rule sets a pairwise limit):
   - $\le$ `excellent_drift_ms` (0.1 ms): `EXCELLENT`
   - $\le$ `max_allowed_drift_ms` (1.0 ms): `ACCEPTABLE`
   - $\le$ `degraded_ceiling_ms` (5.0 ms): `DEGRADED`
   - above that: `BREACH`

4. **Enforce the veto.** Arbitration is permitted only when the worst pair is within
   `max_allowed_drift_ms` **and** the measurement is conclusive. `DEGRADED` is already past
   the limit and is vetoed — it is a severity label for the alert, not a permission to
   continue. `vetoed_pairs` names which regions to isolate; fall back to single-region mode.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Subtracting RTT/2 from measured drift.** Path-delay uncertainty is an *error bound* that
  widens the estimate, never a correction that shrinks it. chrony's own bound is
  `clock_error <= |offset| + root_dispersion + 0.5*root_delay`; RFC 5905 §4 defines the
  synchronization distance as `EPSILON + DELTA/2`, "the maximum error due to all causes".
  Subtracting it manufactures accuracy that was never measured.
- **Treating a dropped probe as a healthy region.** Evaluating the surviving probes and
  proceeding is the failure mode this module exists to prevent; it fails closed instead.
- **Letting a NaN reach the threshold comparison.** Every comparison against NaN is `False`,
  so an unparsed offset leaves the running maximum at 0.0 and reads `EXCELLENT`. Probes are
  rejected with `ClockProbeError` at construction instead.
- **Reading `DEGRADED` as "warning only, keep trading".** Anything above
  `max_allowed_drift_ms` is vetoed. The tier tells the operator how bad it is, not whether
  to continue.
- **Certifying 1 ms agreement over a 70 ms path.** Two nodes disciplined across a
  70 ms-root-delay path carry ±35 ms of offset uncertainty each; a 0.3 ms point estimate
  between them evidences nothing. This is why `is_measurement_conclusive` gates the verdict.
- **Confusing pairwise agreement with UTC accuracy.** Both clocks drifting together is the
  one failure this module is blind to by construction. Pair it with a per-host UTC monitor.
- **Reusing `region_id` across probes.** Pair keys are built from region ids; duplicates
  would silently overwrite entries and hide a breaching pair, so they are now rejected.

## Verification

- 0.3 ms apart on sub-millisecond sync paths → `ACCEPTABLE`, `is_arbitration_allowed` True.
- 2.0 ms apart → `DEGRADED` **and** `is_arbitration_allowed` False (not a warning-only tier).
- 8.0 ms apart → `BREACH`, `CLOCK_UNSYNC_VETO` in `message`.
- 0.3 ms apart but both measured over a 70 ms root-delay path →
  `is_measurement_conclusive` False, arbitration denied.
- Empty or single-probe input → `UNKNOWN`, arbitration denied.
- A NaN or infinite field → `ClockProbeError` at probe construction.
- Run `python -m unittest discover -s skills/cross-datacenter-clock-sync-validation/scripts`.

## Related Skills

- `clock-synchronization-ptp-for-trading-hosts`
- `clock-drift-monitoring-alerting-thresholds`
- `multi-region-active-active-tick-ingestion`
- `market-data-feed-arbitration-across-vendors`
