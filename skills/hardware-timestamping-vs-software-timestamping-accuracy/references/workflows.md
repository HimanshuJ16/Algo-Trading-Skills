# Workflows for Hardware vs Software Timestamping Analysis

## 0. Establish a common timebase before measuring anything

Nothing downstream is meaningful until this holds. On Linux the NIC's PTP hardware clock (PHC) is
an independent clock and the kernel does not convert hardware timestamps to system time.

- Confirm the adapter actually timestamps in hardware: `ethtool -T <iface>` should report
  `SOF_TIMESTAMPING_RX_HARDWARE` / `SOF_TIMESTAMPING_RAW_HARDWARE` and a PTP Hardware Clock index.
  A populated `SO_TIMESTAMPING` field alone proves nothing — the software path returns a value on
  adapters with no hardware support.
- Confirm the PHC is disciplined (`ptp4l`) **and** tied to the system clock (`phc2sys`, or `sfptpd`
  on Solarflare/AMD adapters). Record the daemon's reported offset; it is the input for converting a
  hardware timestamp into the UTC reference.
- Symptom of a missed step: a negative kernel or application capture delay. The engine raises on
  this rather than reporting it, because a packet cannot reach a later layer first.

## 1. Select the applicable RTS 25 Annex row

`trading_activity` selects the divergence/granularity pair:

- `HIGH_FREQUENCY_ALGORITHMIC_TRADING` — 100 µs / 1 µs (Art. 3, Table 2).
- `OTHER_TRADING_ACTIVITY` — 1 ms / 1 ms (Art. 3, Table 2).
- `VOICE_TRADING`, `RFQ_WITH_HUMAN_INTERVENTION`, `NEGOTIATED_TRANSACTION` — 1 s / 1 s.
- `VENUE_GATEWAY_LATENCY_1MS_OR_BELOW` — 100 µs / 1 µs (Art. 2, Table 1).
- `VENUE_GATEWAY_LATENCY_ABOVE_1MS` — 1 ms / 1 ms (Art. 2, Table 1).

`max_divergence_nanos` and `required_granularity_nanos` overrides are validated one-way: they are
accepted only when stricter than the selected row, so a configuration mistake cannot quietly widen a
regulatory tolerance.

## 2. Declare the timestamping point

`recorded_timestamp_source` is `HARDWARE_MAC`, `KERNEL_STACK`, or `APPLICATION`. This is not a
reporting preference — it decides the verdict. The same packet with a well-disciplined NIC clock is
compliant recorded at the MAC and non-compliant recorded in user space after a 180 µs scheduler
stall. RTS 25 Art. 4 requires this point to be documented, so the engine requires it to be stated.

## 3. Ingest samples

`PacketTimestampSample` carries the three layer timestamps, the traceable UTC reference for the same
arrival, and the granularity the recorded field resolves. Validation rejects non-`int` values
(`float` cannot hold a nanosecond epoch — the float64 spacing at ~1.7e18 ns is 256 ns), `bool`,
negative epochs, blank ids, and a granularity below 1 ns.

The UTC reference must be independent of the host under audit: a GNSS-disciplined capture appliance
on a tap, or the hardware timestamp corrected by the PTP daemon's PHC-to-UTC offset. Feeding the
host's own `CLOCK_REALTIME` back in compares a clock against itself and always passes.

## 4. Decompose

Capture-path delays (elapsed time, always ≥ 0 on a common timebase):

- $\Delta t_{\text{kernel}} = T_{\text{kernel}} - T_{\text{hw}}$ — driver/softirq receive path.
- $\Delta t_{\text{app}} = T_{\text{app}} - T_{\text{kernel}}$ — wakeup, scheduling, user-space read.
- $\Delta t_{\text{sw}} = T_{\text{app}} - T_{\text{hw}}$ — total software distortion of the
  observed arrival time.

Signed errors versus UTC (clock quality, not latency):

- $\delta_{\text{hw}} = T_{\text{hw}} - T_{\text{UTC\_ref}}$ — the RTS 25 business-clock divergence.
- $\delta_{\text{kernel}}$, $\delta_{\text{app}}$ — the same layer's recorded value versus true UTC,
  each equal to $\delta_{\text{hw}}$ plus that layer's cumulative capture delay.

The sign is retained throughout. A clock 40 µs ahead of UTC and one 40 µs behind are different
faults with different remediations; a magnitude alone cannot distinguish them.

## 5. Audit

- Divergence: $|\delta_{\text{recorded}}| \le$ the row's bound.
- Granularity: recorded field granularity $\le$ the row's bound.
- `rts25_verdict`: `COMPLIANT` / `NON_COMPLIANT_DIVERGENCE` / `NON_COMPLIANT_GRANULARITY` /
  `NON_COMPLIANT_DIVERGENCE_AND_GRANULARITY`.
- `status` compares the hardware and application layers across four states, including
  `HARDWARE_EXCEEDS_APPLICATION_WITHIN_LIMIT` — reachable when a clock running behind UTC is partly
  cancelled by capture delay, and a state that must never be collapsed into "both failed".

Log levels follow severity: an out-of-tolerance hardware clock is `CRITICAL` (no choice of
timestamping point rescues it), a software-layer breach on a healthy clock is `WARNING`.
`analyze_batch` suppresses the per-packet line and emits one summary instead — a clock failure
across a large capture would otherwise flood the log with one `CRITICAL` line per packet,
during exactly the incident whose logs matter.

## 6. Benchmark the distribution

`analyze_batch` returns `TimestampBenchmarkSummary`:

- |divergence| and |recorded error| at p50 / p99 / max.
- Kernel and total software capture delay at p50 / p99, plus min, max, and peak-to-peak jitter.
- `divergence_breach_count`, `granularity_breach_count`, `rts25_compliant_sample_count`, and the
  worst packet id.

Percentiles use the **nearest-rank** definition, so every figure in the audit artifact is a value
some packet actually exhibited rather than an interpolation. Peak-to-peak jitter (max − min) is
reported next to the percentiles because a single stall dominates it.

Read the p99 and the breach count, not the median: RTS 25 bounds the divergence at the instant every
timestamp is applied, so one breach in a batch is a breach.

## 7. Retain the evidence

Store the per-packet reports and the summary alongside the PTP daemon's offset history, the
`ethtool -T` capability output, and the documented timestamping point. That bundle — not this
report on its own — is what an Art. 4 traceability review reads, together with the annual
compliance review it also requires.
