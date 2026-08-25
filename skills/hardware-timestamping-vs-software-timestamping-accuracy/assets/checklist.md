# Pre-Flight Checklist

- [ ] Does `ethtool -T <iface>` report hardware timestamping capabilities and a PTP Hardware Clock index, rather than hardware capture being assumed from a populated `SO_TIMESTAMPING` field?
- [ ] Are `ptp4l` and `phc2sys` (or `sfptpd`) running, so the NIC PHC and the system clock share a timebase — and is the daemon's reported offset being recorded?
- [ ] Are all four timestamps for each packet integer nanoseconds on that common timebase, with no `float` conversion anywhere in the path?
- [ ] Does the UTC reference come from a source **independent** of the host under audit (GNSS-disciplined tap appliance, or the hardware timestamp corrected by the PTP daemon's PHC-to-UTC offset), and not from the host's own `CLOCK_REALTIME`?
- [ ] Are capture-path delays reported separately from clock divergence, so a scheduler stall is never described as clock drift?
- [ ] Is any negative inter-layer delay treated as a timebase misconfiguration and fixed, rather than reported as a latency?
- [ ] Is divergence from UTC reported **signed**, so a clock ahead of UTC is distinguishable from one behind?
- [ ] Is the RTS 25 Annex row selected from the entity and activity actually being audited, rather than defaulting to 100 µs for everything?
- [ ] Is the recorded-field **granularity** audited against the Annex bound as well as the divergence — a clock inside 100 µs recorded into a millisecond field still fails?
- [ ] Is the timestamping point that enters the reportable record documented and passed to the engine as `recorded_timestamp_source` (RTS 25 Art. 4)?
- [ ] Is compliance assessed over a distribution — p99 and breach count, not a single packet or a healthy median?
- [ ] Are the per-packet reports, the summary, the PTP offset history, and the documented traceability chain retained together, with the annual Art. 4 compliance review scheduled?
