# Workflows for Clock Drift Monitoring

## 1. Daemon configuration

- Run `ptp4l` bound to a hardware-timestamping NIC (`-H`), not software timestamping
  (`-S`), and run `phc2sys` as well if reportable events are stamped from
  `CLOCK_REALTIME` rather than directly from the PHC. Configuring that stack is
  `clock-synchronization-ptp-for-trading-hosts`; this skill consumes its output.
- Have the daemon write its telemetry somewhere the monitor can read without competing
  with the trading path — a log file, a Unix socket, or the management interface.

## 2. Mapping daemon output to `PtpState`

`ptp4l` reports IEEE 1588 port states and servo states; it does not report holdover. The
mapping this skill assumes:

| Daemon observation | `PtpState` |
|---|---|
| Port `SLAVE`, servo `s2` (locked) | `LOCKED` |
| Selected grandmaster lost / Sync messages stopped, servo still free-running on the local oscillator | `HOLDOVER` |
| Port `FAULTY`, `LISTENING`, `UNCALIBRATED`, `INITIALIZING`, or servo `s0` (unlocked) | `UNLOCKED` |

`s1` (clock step) means the servo is jumping the clock rather than slewing it. Treat it as
`UNLOCKED` unless you have deliberately decided a step is tolerable, because timestamps
either side of a step are not on a continuous time base.

Getting this mapping wrong is the failure that survives every test: the monitor works
perfectly on a state it is never actually sent.

## 3. Units

`master offset` is in **nanoseconds**. Convert before evaluating:

```python
from clock_drift_monitor import (
    ClockDriftMonitor, ClockTelemetryError, PtpState, offset_us_from_ptp4l_ns,
)

offset_us = offset_us_from_ptp4l_ns(parsed_master_offset_ns)
```

Passing the nanosecond figure directly under-reports drift 1000×, and the monitor will
never fire.

## 4. Monitor process

Run `ClockDriftMonitor` as a sidecar on the trading host, in a single-threaded polling
loop. It is not thread-safe.

```python
monitor = ClockDriftMonitor(
    kill_switch_callback=halt_trading_engine,
    warning_threshold_us=50.0,
    critical_threshold_us=90.0,       # below the 100us RTS 25 ceiling: headroom
    holdover_grace_s=30.0,            # measured: 90us limit / 3us-per-second drift
    max_telemetry_age_s=2.0,          # ~4x the 500ms poll interval
)

while running:
    reading = read_next_ptp_telemetry()          # may return None
    if reading is not None:
        try:
            monitor.process_telemetry(
                offset_us=offset_us_from_ptp4l_ns(reading.offset_ns),
                ptp_state=map_state(reading),
            )
        except ClockTelemetryError:
            logger.exception("Unusable PTP telemetry; clock state unknown")
            # An unparseable reading is a fault. Do not `continue` silently —
            # let the staleness deadline below catch a persistent parse failure.
    monitor.check_liveness()                     # every tick, including empty ones
    sleep(poll_interval)
```

`check_liveness()` is called outside the `if`, deliberately. That is the only path that
notices a daemon which has stopped producing output altogether.

## 5. Threshold selection

1. Identify the RTS 25 Table 2 row for the activity (or FINRA 6820 if US) — see
   `references/standards.md`. That number is the **ceiling**.
2. Subtract headroom for detection: `poll_interval + halt_latency` multiplied by the
   worst observed drift rate. The result is `critical_threshold_us`.
3. Set `warning_threshold_us` from the observed offset distribution — high enough that
   ordinary network jitter does not page anyone, low enough to give warning before the
   halt point.
4. Derive `holdover_grace_s = critical_threshold_us / measured_drift_us_per_second`. If
   the drift rate has not been measured, leave the fail-closed `0.0`.

## 6. Action dispatching and recovery

- On `CRITICAL`, the callback runs synchronously. Keep it short and make it the *stop*
  path only — an IPC command or a socket write to the engine, not a report generator.
- If the callback raises, the monitor stays latched and re-raises. Treat that as a manual
  escalation: the engine may still be live.
- Log the UTC time of the breach for the regulatory record, while noting in that record
  that the wall clock at the moment of a clock breach is itself suspect. The monotonic
  elapsed values in the reason string (`HOLDOVER_EXPIRED_30.500s`,
  `TELEMETRY_STALE_6.000s`) are the trustworthy part.
- Identify the reportable events stamped during the breach window and remediate them
  before resuming. Then `reset(operator, reason)` — both arguments are required and both
  are logged, because the RTS 25 Article 4 annual review needs an attributable record of
  who restarted origination and why.
