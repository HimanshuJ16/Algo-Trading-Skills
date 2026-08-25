# Standards for Hardware vs Software Timestamping

## Engineering Standards

| Metric | Engineering Standard |
|---|---|
| Quantity Separation | Clock divergence from UTC and capture-path delay MUST be reported as separate figures. $T_{\text{app}} - T_{\text{UTC\_ref}}$ is divergence **plus** elapsed processing time; describing it as "drift" attributes a scheduler stall to the time source. |
| Common Timebase | All layer timestamps for a packet MUST be on one timebase before any subtraction. The Linux kernel does **not** convert NIC hardware timestamps to system time — the adapter's PTP hardware clock (PHC) is an independent clock, and `phc2sys`/`sfptpd` is what ties it to `CLOCK_REALTIME`. |
| Ordering Guard | A later capture point MUST NOT precede an earlier one. $\Delta t < 0$ between layers is the observable signature of an undisciplined PHC and MUST raise rather than be reported as a negative latency. |
| Signed Divergence | Divergence from UTC MUST be reported signed. A clock ahead of UTC timestamps events before they occurred; `abs()` erases the direction that identifies the fault. |
| Integer Nanoseconds | Timestamps MUST be integer nanoseconds. IEEE 754 binary64 carries a 53-bit significand, so the representable spacing at an epoch magnitude of ~1.7e18 ns is 256 ns — coarser than the effect being measured. |
| Declared Timestamping Point | The layer whose timestamp enters the reportable record MUST be declared and audited explicitly; the compliance answer differs by layer for the same packet. This is the operational form of RTS 25 Art. 4. |
| Independent UTC Reference | The UTC reference MUST be independent of the host under audit (GNSS-disciplined capture appliance on a tap, or the hardware timestamp corrected by the PTP daemon's reported PHC-to-UTC offset). A host's own clock compared against itself measures nothing. |
| Distributional Evidence | Compliance and jitter MUST be assessed over a distribution (p50/p99/max, breach counts), not a single sample. Peak-to-peak capture jitter (max − min) MUST be reported alongside percentiles, since it is dominated by single outliers. |
| Granularity As A Separate Bound | Recorded-field granularity MUST be audited independently of divergence. A clock inside 100 µs recorded into a millisecond field still breaches RTS 25. |
| Hardware Capture Verification | Hardware timestamping MUST be confirmed from the adapter's reported capabilities (`ethtool -T`, `SOF_TIMESTAMPING_RX_HARDWARE` / `SOF_TIMESTAMPING_RAW_HARDWARE`), not inferred from a populated `SO_TIMESTAMPING` field — the software path returns a value regardless. |

## Regulatory Anchors (verify currency before relying on them)

MiFID II RTS 25 bounds the **business clocks** used to record reportable events, at the instant a
timestamp is applied. It is an obligation on the entity's clocks and on its documented traceability
to UTC — a latency-decomposition report is evidence toward that file, never a substitute for it.
RTS 25 is technology-neutral: it names no synchronization protocol, and its finest granularity row
is 1 microsecond. Nanosecond precision is an engineering choice, not a regulatory requirement.

| Regime | Provision | Requirement |
|---|---|---|
| EU — MiFID II RTS 25 | Commission Delegated Regulation (EU) 2017/574, Art. 2 + Annex Table 1 (trading venue operators) | Gateway-to-gateway latency above 1 ms: maximum divergence from UTC **1 millisecond**, granularity **1 ms or better**. Latency of 1 ms or below: divergence **100 microseconds**, granularity **1 microsecond or better**. |
| EU — MiFID II RTS 25 | Reg. (EU) 2017/574, Art. 3 + Annex Table 2 (members/participants) | High-frequency algorithmic trading technique: divergence **100 µs**, granularity **1 µs or better**. Voice trading, RFQ with human intervention, negotiated transactions: divergence **1 second**, granularity **1 s or better**. Any other trading activity: divergence **1 millisecond**, granularity **1 ms or better**. |
| EU — MiFID II RTS 25 | Reg. (EU) 2017/574, Art. 4 | Traceability to UTC must be documented: the system design and functioning, **the exact point at which a timestamp is applied**, and a review of compliance at least annually. |
| EU — MiFID II RTS 25 | Reg. (EU) 2017/574, Art. 1 | Reference time is UTC issued and maintained by the timing centres listed in the BIPM annual report; a member may also use UTC disseminated by a satellite system, provided any offset from UTC is accounted for and removed. |

The engine encodes both Annex tables in `RTS25_ACCURACY_REQUIREMENTS`. Overrides are accepted only
where they **tighten** a row, so an internal target can be stricter than the obligation but a
regulatory bound can never be relaxed by configuration.

Other regimes use different tolerances and are **not** interchangeable with the figures above — US
CAT and FINRA Rule 4590 in particular. See `cross-vendor-timestamp-precision-reconciliation`
(`references/standards.md`) for those figures before reusing this engine outside the EU regime.

## Platform Notes

| Surface | Observed behaviour | Note |
|---|---|---|
| Linux `SO_TIMESTAMPING` | `SOF_TIMESTAMPING_RX_HARDWARE` captures at the adapter; `SOF_TIMESTAMPING_RX_SOFTWARE` captures as data enters the kernel receive stack; `SOF_TIMESTAMPING_RAW_HARDWARE` reports the raw adapter timestamp. | The kernel documentation states hardware timestamps are **not** converted to system time and recommends exposing the NIC clock as a PTP clock source so userspace can convert. Enabling `SO_TIMESTAMP` alongside `SO_TIMESTAMPING` can cause a substitute software timestamp to be generated when a real one is missing. |
| PTP hardware clock (PHC) | An independent clock on the adapter. `phc2sys` synchronizes the system clock to the PHC (or the reverse); with hardware timestamping it is a separate daemon step from `ptp4l`. | This is why a cross-layer subtraction without a disciplined PHC measures a clock offset rather than a stack delay, and can go negative. |
| Solarflare / AMD adapters (e.g. XtremeScale X2522) | Vendor material describes PTP support for packet timestamping at single-digit nanosecond **resolution**, with `sfptpd` disciplining adapter and system clocks. | Resolution is not accuracy. End-to-end accuracy versus UTC is set by the whole traceability chain (grandmaster, distribution, asymmetry, holdover), and must be measured on the deployed host rather than taken from a datasheet figure. This skill deliberately encodes no vendor accuracy number. |

Sources consulted (Aug 2026): Commission Delegated Regulation (EU) 2017/574 Arts. 1-4 and Annex
Tables 1-2 (CELEX:32017R0574, cross-checked against two independent secondary summaries of the Annex
and against `cross-vendor-timestamp-precision-reconciliation/references/standards.md` in this repo);
Linux kernel networking documentation, `docs.kernel.org/networking/timestamping.html`, on
`SO_TIMESTAMPING` flags and the statement that hardware timestamps are not converted to system time;
Linux kernel PTP hardware clock documentation and Red Hat / SUSE PTP administration guides on
`ptp4l`/`phc2sys` and PHC independence; AMD/Xilinx Solarflare X2522 product material and the
`Xilinx-CNS/sfptpd` project for adapter timestamping and clock-discipline behaviour. The float64
spacing figure was reproduced in CPython 3.11.
