# Pre-Flight Checklist

## Inputs

- [ ] Is `baseline_latency_us` the mean **service time** $10^6/C$, and not an observed
      round-trip or wire-to-wire latency?
- [ ] Is load-independent latency (transit, serialisation, gateway hops) passed as
      `fixed_latency_us` rather than folded into `baseline_latency_us`?
- [ ] Is `service_time_consistency_ratio` on the report close to 1.0 — and is the
      `SERVICE TIME INCONSISTENT` warning surfaced rather than filtered out of the logs?
- [ ] Is capacity taken from the **market segment / partition** carrying the instrument,
      rather than a venue-wide headline number?
- [ ] Is every input a documented measurement, with assumed values flagged as assumptions?

## Arrival rate

- [ ] Is $\lambda$ measured over a burst-scale window (sub-second to seconds), not a
      session average?
- [ ] Is the **peak** of that window used, not its mean?
- [ ] Is aggregate partition load kept distinct from this session's own message rate?

## Model

- [ ] Is $\rho = \lambda/C$ recomputed in real time, not on a stale snapshot?
- [ ] Is the service model (`M/M/1` vs `M/D/1`) chosen deliberately per venue and held
      fixed — with the understanding that M/D/1 is exactly half the M/M/1 queueing term?
- [ ] Is it understood that Poisson arrivals make this model **optimistic** during clustered
      bursts?
- [ ] Is `effective_latency_is_lower_bound` checked **before** `effective_latency_us` is
      read or compared? (It binds at $\rho > 0.99$, i.e. before `is_saturated` does.)
- [ ] Is it understood that at $\rho \ge 1$ the latency is a censored lower bound, so
      $\rho = 1.5$ and $\rho = 10$ return the same figure?

## Directives

- [ ] Are the 0.50 / 0.85 bands calibrated against realised mark-outs, rather than adopted
      as given?
- [ ] Are the thresholds validated at construction (`0 < moderate < high <= 1`) so an
      inverted pair cannot silently emit the wrong directive?
- [ ] Does `PAUSE_PASSIVE_QUOTING` also drive an unwind path for **resting** inventory, not
      just a stop on new quotes?
- [ ] Is spread widening sized from the reported `queuing_delay_penalty_us`, rather than a
      fixed tick count?
- [ ] Is there hysteresis (dwell time or lower re-entry threshold) so a $\rho$ oscillating
      around a band edge cannot flap the directive?

## Beyond the model

- [ ] Are venue **reject** rates and reason codes monitored alongside $\rho$ — since above a
      session throttle the venue rejects rather than queues?
- [ ] Are consecutive rejects tracked against the venue's documented disconnect limit?
- [ ] Is there a session re-establishment and order-state resynchronisation path, and does
      it reconcile resting orders before re-quoting?
- [ ] Is retry-on-reject bounded, so a throttle breach cannot escalate into a disconnect?

## Data hygiene

- [ ] Are non-finite or negative telemetry readings rejected at construction and counted as
      quarantined samples, rather than allowed to resolve to a `LOW` risk verdict?
- [ ] Are `MatchingEngineLoadAuditReport` records retained as the audit trail for why
      quoting stopped?
