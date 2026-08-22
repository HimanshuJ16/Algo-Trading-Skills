# Pre-Flight Checklist: Clock Drift Monitoring

## Scope and thresholds
- [ ] **Right row, right regime**: Is `critical_threshold_us` taken from the RTS 25
      Table 2 row for the activity actually run (100 µs HFT technique / 1 ms other algo),
      or from FINRA 6820 (50 ms) for a US CAT reporter — rather than from the 100 µs
      headline?
- [ ] **Headroom below the ceiling**: Is the halt threshold set *below* the regulatory
      maximum by at least the drift accumulated over `poll_interval + halt_latency`? A
      halt that fires exactly at the limit fires after the non-compliant timestamps exist.
- [ ] **Warning reachable and not noisy**: Is `warning_threshold_us` strictly below
      critical (the constructor enforces this) and above the segment's ordinary jitter?
- [ ] **Granularity too**: RTS 25 requires 1 µs timestamp granularity alongside 100 µs
      divergence. Does the recording path actually record at that granularity? This
      monitor does not check it.

## Telemetry integrity
- [ ] **Units converted**: Is `offset_us_from_ptp4l_ns()` applied to every `master offset`
      reading? Raw nanoseconds under-report drift 1000× and the monitor never fires.
- [ ] **Non-finite rejected**: Does the polling loop catch `ClockTelemetryError` and treat
      it as a fault rather than skipping the reading? `abs(nan)` clears every threshold.
- [ ] **State mapping written down**: Is the daemon-to-`PtpState` mapping documented and
      tested? `ptp4l` has no `HOLDOVER` state; an unmapped fault silently arrives as
      `LOCKED`.
- [ ] **Hardware timestamping**: Is the offset sourced from PTP hardware timestamping at
      the NIC, not NTP or software timestamping? Millisecond OS jitter cannot evidence a
      100 µs bound.
- [ ] **Absolute values**: Does evaluation use `|offset|` so negative drift is caught?
      (The module does; confirm your own parser does not drop the sign earlier.)

## Liveness and holdover
- [ ] **Staleness deadline set**: Is `max_telemetry_age_s` configured, at a few multiples
      of the poll interval? Unset, a dead `ptp4l` reads as permanent health.
- [ ] **`check_liveness()` on every tick**: Including ticks where no telemetry arrived?
      Called only alongside a successful read, it can never detect silence.
- [ ] **Holdover grace measured, not guessed**: Is `holdover_grace_s` derived as
      `critical_threshold_us / measured_drift_us_per_second`, with the drift rate measured
      on this hardware under rack thermal conditions? If not measured, is it left at the
      fail-closed `0.0`?
- [ ] **Monotonic time source**: Is the injected `monotonic_clock` genuinely monotonic?
      Timing a clock fault with the faulty wall clock is circular.

## Halt path
- [ ] **Automated, not human-in-the-loop**: Does `CRITICAL` reach the engine's stop path
      directly? At HFT rates, thousands of non-compliant events are stamped before anyone
      reads a chat alert.
- [ ] **Callback is the stop path only**: Short, synchronous, no reporting or I/O that can
      block behind the thing being halted.
- [ ] **Callback failure escalates**: If the callback raises, the monitor latches and
      re-raises — does the supervising loop turn that into a paged manual halt rather than
      a logged warning? The engine may still be live.
- [ ] **Out-of-band alerting**: Does the alert path survive the trading engine being down?

## Recovery and audit
- [ ] **Latch respected**: Is resumption gated on `reset(operator, reason)` rather than on
      the offset merely returning to normal? A clock that breached and recovered still
      wrote bad timestamps.
- [ ] **Breach window identified**: Are the reportable events stamped during the breach
      enumerated and remediated before origination resumes?
- [ ] **Attribution recorded**: Are the operator and reason from `reset()` captured in a
      durable log, not just process stdout?
- [ ] **Article 4 review**: Is the traceability system's design, functioning and
      specification documented, is the exact timestamp application point identified, and
      is the annual compliance review scheduled? The monitor is evidence inside that
      system, not the system.
