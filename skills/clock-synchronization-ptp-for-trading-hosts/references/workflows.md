# Workflows for PTP Clock Synchronization

Commands below are illustrative. Every transport, domain and rate value must be taken from
the grandmaster's documented PTP profile — see `standards.md` §3.

## 1. Interface audit (before any config)

```bash
ethtool -T eth0
```

Require, in the output:

- `SOF_TIMESTAMPING_TX_HARDWARE`, `SOF_TIMESTAMPING_RX_HARDWARE`, `SOF_TIMESTAMPING_RAW_HARDWARE`
- `PTP Hardware Clock:` a numeric index, **not** `none`

If the PHC index is `none`, the NIC has no hardware clock. No `ptp4l` flag fixes that; the
remedy is different hardware.

## 2. Establish the grandmaster's profile

Record, from the grandmaster's own documentation, before writing `ptp4l.conf`:

| Parameter | Why it must match |
|---|---|
| Transport (L2 / UDPv4 / UDPv6) | Mismatch = the port never leaves `LISTENING`. |
| `domainNumber` | Mismatch on a shared segment can silently select the *wrong* grandmaster. |
| Delay mechanism (E2E / P2P) | RFC 9760 mandates E2E; G.8275.1 forbids Pdelay. |
| Sync / Announce / Delay_Req intervals | Drives the log cadence, and therefore `max_sample_age_s`. |
| One-step / two-step | Receivers should support both; transmitters may do either. |

## 3. `ptp4l` daemon launch

```bash
# UDPv4 (linuxptp default transport; Enterprise Profile grandmasters):
ptp4l -i eth0 -H -s -m

# IEEE 802.3 / L2 (G.8275.1 telecom-profile grandmasters):
ptp4l -i eth0 -2 -H -s -m
```

- `-H` hardware timestamping — already the default, stated explicitly so a later config
  change cannot silently demote the host to software timestamping.
- `-s` `clientOnly`. Without it the host can win a BMCA election and start serving time to
  the segment from its own free-running oscillator.
- `-m` print to stdout, for ingestion by `PtpClockSyncManager`.
- Do **not** add `-S`. It selects software timestamping and cannot support a
  microsecond-scale accuracy claim.

Watch the port walk `INITIALIZING → LISTENING → UNCALIBRATED → SLAVE` and the servo walk
`s0 → s1 → s2`. A port parked in `LISTENING` almost always means a transport, domain or
delay-mechanism mismatch, not a network fault.

## 4. `phc2sys` daemon launch

```bash
phc2sys -s /dev/ptp0 -c CLOCK_REALTIME -w -m
```

`-w` is not optional convenience. The PHC runs on the PTP timescale (TAI, no leap seconds);
`-w` (absent `-O`) keeps the sink-to-source offset updated from the `currentUtcOffset`
obtained from `ptp4l`. Without `-w` and without `-O`, `CLOCK_REALTIME` is disciplined onto
TAI and sits a whole number of seconds from UTC while every offset reads nominal.

Use `-O <seconds>` only when you are deliberately supplying the offset yourself and have a
process that maintains it across leap-second changes.

## 5. Telemetry ingestion

Route both daemons' stdout (or their journal/log files) into a single reader loop:

```python
manager = PtpClockSyncManager(
    max_allowed_offset_ns=MIFID_HFT_MAX_DIVERGENCE_NS,  # the row that binds YOU
    max_sample_age_s=5.0,                               # your log interval x tolerance
)
for line in stream:
    manager.parse_log_line(line)
    verdict = manager.evaluate_compliance()
```

Handle in the loop:

- `verdict["reasons"]` — the ordered, human-readable causes of a non-passing verdict.
- `verdict["combined_offset_ns"]` — the end-to-end bound. Alert on this, not on
  `max_offset_ns`.
- `verdict["unparsed_telemetry_lines"]` — non-zero means the daemon is emitting a format
  the parser does not recognise. Investigate; do not ignore. A blind parser looks identical
  to a healthy clock right up until the audit.
- `verdict["telemetry_stale"]` — a dead daemon, which is the more common failure than drift.

**Keep at least one per-sample logging channel.** With `summary_interval` (or `phc2sys -u`)
the daemons print `rms N max M freq …` lines that carry **no servo state**. The manager will
not invent one: `ptp4l_state`/`phc2sys_state` stay `UNKNOWN` and the verdict fails closed
forever. That is the correct behaviour — a summary line is not evidence the servo is locked —
but it means a summary-only configuration is unusable for this check. Either keep per-sample
lines, or accept that only the offset magnitudes (from `max`, never `rms`) are being tracked
and source the lock state elsewhere.

## 6. Escalation

This module reports. Threshold escalation, holdover grace and the latched halt are
`clock-drift-monitoring-alerting-thresholds` — do not implement a second, divergent set of
thresholds here. Convert units at that boundary: this module is in nanoseconds, that one is
in microseconds.

## 7. Periodic out-of-band verification

On a documented schedule (RTS 25 Article 4 requires at least annual review of the
traceability system, which is a floor, not a cadence):

- Compare `CLOCK_REALTIME` against an **independent** UTC reference — a second grandmaster
  on a different domain, or a GNSS receiver not feeding the primary grandmaster.
- Confirm the selected grandmaster's clock identity and `clockClass` are the expected ones.
- Confirm the TAI-UTC offset in use matches the current published value.
- Characterise path asymmetry after any change to cabling, switch path or NIC. PTP cannot
  observe asymmetry, and half of it becomes fixed, invisible offset error.

None of these can be inferred from offset telemetry, which is why they are a separate
procedure rather than a dashboard panel.
