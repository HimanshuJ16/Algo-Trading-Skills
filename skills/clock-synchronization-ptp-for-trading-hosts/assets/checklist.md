# Pre-Flight Checklist — PTP Clock Synchronization

## Hardware

- [ ] `ethtool -T <iface>` shows `SOF_TIMESTAMPING_TX_HARDWARE`, `SOF_TIMESTAMPING_RX_HARDWARE`
      and `SOF_TIMESTAMPING_RAW_HARDWARE`.
- [ ] `PTP Hardware Clock` is a numeric index, not `none`.
- [ ] Path asymmetry to the grandmaster has been characterised by measurement, and re-measured
      after the most recent cabling or switch-path change.

## Profile match (all four taken from the grandmaster's documentation, not a tutorial)

- [ ] Transport matches — `-2`/L2 for a G.8275.1 grandmaster, UDP for an Enterprise Profile
      (RFC 9760) one. Not chosen by habit.
- [ ] `domainNumber` matches. (A wrong domain on a shared segment can select the wrong
      grandmaster silently, unlike a wrong transport which fails loudly.)
- [ ] Delay mechanism matches (E2E vs P2P).
- [ ] Sync / Announce / Delay_Req intervals match, and `max_sample_age_s` was derived from them.

## Daemons

- [ ] `ptp4l` runs with `-H` stated explicitly and **without** `-S`.
- [ ] `ptp4l` runs with `-s` (`clientOnly`) so the host can never serve time to the segment.
- [ ] `phc2sys` is running at all — `ptp4l` alone leaves `CLOCK_REALTIME` free-running.
- [ ] `phc2sys` runs with `-w` (or a deliberately maintained `-O`). **Without it the system
      clock is disciplined onto TAI and sits whole seconds from UTC while every offset reads
      nominal.**
- [ ] Port state observed reaching `SLAVE`/`TIME_RECEIVER` and servo state reaching `s2` —
      not merely a small offset printed in `s0`.

## Monitoring

- [ ] Both daemons' output is fed to `PtpClockSyncManager`.
- [ ] `max_sample_age_s` is set. With it unset, a dead daemon reports healthy forever.
- [ ] Alerting keys off `combined_offset_ns` (the serial sum), **not** `max_offset_ns`.
- [ ] `unparsed_telemetry_lines` is exported and alerted on — a blind parser is
      indistinguishable from a healthy clock.
- [ ] The configured `max_allowed_offset_ns` comes from the regulatory row that binds this
      firm's activity and jurisdiction, with headroom below it for detection and halt latency
      — not from a copied default.
- [ ] Escalation is wired to `clock-drift-monitoring-alerting-thresholds`, with the ns → µs
      conversion done at the boundary.

## Out-of-band verification (offset telemetry structurally cannot do these)

- [ ] `CLOCK_REALTIME` compared against an independent UTC reference on a documented schedule.
- [ ] Selected grandmaster identity and `clockClass` confirmed to be the expected ones.
- [ ] TAI-UTC offset in use matches the current published value.
- [ ] The RTS 25 Article 4 traceability documentation — design, functioning, specifications,
      and the exact point at which each timestamp is applied — exists and has been reviewed
      within the last year.
