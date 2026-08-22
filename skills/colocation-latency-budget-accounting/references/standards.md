# Standards for Latency Budget Accounting

## Timestamp acquisition

| Requirement | Standard | Source |
|---|---|---|
| Hardware timestamping | $T_0$ (ingress) and $T_5$ (egress) MUST originate from NIC hardware timestamps. On Linux these are requested with `SO_TIMESTAMPING` using `SOF_TIMESTAMPING_RX_HARDWARE` / `SOF_TIMESTAMPING_TX_HARDWARE`; driver support is queried with `ethtool -T <iface>`. | Linux kernel networking documentation, *Timestamping* (`SO_TIMESTAMPING`) |
| Clock domain | NIC hardware timestamps are taken in the adapter's own clock domain, exposed as a PTP hardware clock (PHC). They are **not** in the `CLOCK_MONOTONIC` or `CLOCK_REALTIME` domain and MUST be converted to a common time base before being differenced against software timestamps. | Linux kernel networking documentation, *Timestamping* — hardware timestamps require conversion to system time, and the kernel recommends exposing the NIC clock as a PTP clock source for userspace conversion |
| In-host timer | $T_1 \dots T_4$ MUST use a monotonic source. `CLOCK_REALTIME` (Python `time.time()`) is steppable by NTP and can move backwards. | POSIX `clock_gettime` clock definitions |
| `rdtsc` | `rdtsc` yields cycle counts, not nanoseconds. Conversion requires a calibrated invariant-TSC frequency; invariant TSC (constant rate across ACPI P-, C- and T-states) is advertised by `CPUID.80000007H:EDX[8]`. Without it, frequency scaling and core migration corrupt the measurement. | Intel® 64 and IA-32 Architectures Software Developer's Manual, Vol. 3B, *Time-Stamp Counter* |
| Clock distribution | Where multiple hosts' traces are compared, the PHCs MUST be disciplined to a common grandmaster. | IEEE 1588 (Precision Time Protocol) |

## Hot-path discipline

| Requirement | Standard |
|---|---|
| Zero hot-path allocation | Timestamp recording in the hot path MUST NOT allocate heap memory, take a lock, or execute a blocking call. Push to a fixed-size lock-free ring buffer and account out-of-band. |
| No in-thread aggregation | Percentile computation, logging and SLA auditing MUST run off the hot path. This module is out-of-band code. |
| Fail-loud ingestion | A trace whose timestamps are not non-decreasing MUST be rejected and counted, never clamped. A negative phase duration is an instrumentation or clock-domain defect. |

## Tail metrics

| Requirement | Standard |
|---|---|
| Tail primacy | Performance SLAs MUST be evaluated against $P_{99}$ and $P_{99.9}$, never the arithmetic mean. |
| Sample sufficiency | A $q$-percentile needs on the order of $1/(1-q)$ observations before it is a measurement rather than an interpolation between the top two samples: ~100 for $P_{99}$, ~1,000 for $P_{99.9}$. Report the sample count alongside every tail figure. |
| Interpolation method | This module uses NumPy's default linear interpolation, so a reported percentile may be a value that was never observed. Where an SLA must be evidenced against observed samples, use a nearest-rank percentile instead. |
| Threshold semantics | `is_sla_breach` is a strict `>`: a trace equal to the budget consumes it exactly and is not a breach. |

## Regulatory context (scope note)

This module measures **relative** in-host latency. It is not a business-clock timestamping record.

| Jurisdiction | Instrument | Requirement | Applicability |
|---|---|---|---|
| EU | Commission Delegated Regulation (EU) 2017/574 (MiFID II RTS 25), Annex, Table 2 | Members/participants of a trading venue applying a **high-frequency algorithmic trading technique**: maximum divergence from UTC of **100 microseconds**, timestamp granularity of **1 microsecond or better**. Other automated activity falls under the 1 millisecond catch-all. | Mandatory for in-scope EU trading venue members. Applies to timestamping of *reportable events* against UTC — a UTC-traceable business clock — not to the `CLOCK_MONOTONIC` deltas computed here. |

Firms must also be able to demonstrate traceability to UTC by documenting system design and identifying the exact, consistent point at which a timestamp is applied. Co-located HFT hosts therefore generally need *both*: a UTC-traceable business clock for regulatory timestamping, and the relative monotonic instrumentation this module consumes for engineering. Do not substitute one for the other.

Jurisdictions outside the EU impose different clock-synchronisation regimes; nothing above should be generalised beyond MiFID II scope.

## Sources

- Linux kernel documentation — *Timestamping*: https://www.kernel.org/doc/html/latest/networking/timestamping.html
- Commission Delegated Regulation (EU) 2017/574 (RTS 25): https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX%3A32017R0574
- IEEE 1588 — Precision Time Protocol: https://standards.ieee.org/ieee/1588/6825/
- Intel® 64 and IA-32 Architectures Software Developer's Manual: https://www.intel.com/content/www/us/en/developer/articles/technical/intel-sdm.html
