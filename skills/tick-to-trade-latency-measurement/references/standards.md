# Standards & Sources for Tick-to-Trade Latency Measurement

## There is no published tick-to-trade latency standard

**No regulator, exchange or standards body publishes a mandatory tick-to-trade latency
SLA, and none publishes per-stage T2T budgets.** A table headed "Institutional
Tick-to-Trade Latency Standards" giving hard per-stage budgets ($< 300\ \text{ns}$ NIC
ingress, $< 2.5\ \mu\text{s}$ total, and so on) has no source behind it — figures like
those are engineering illustration presented as a standard. T2T budgets are set by each
firm against its own venue, colocation, strategy and hardware.

The nearest thing to an industry reference point is a **benchmark**, not a requirement:
the STAC Benchmark Council's **STAC-N1** suite, which measures network-stack latency
under a simulated market-data workload and reports mean, 99th percentile and maximum at
a stated message rate. Published STAC-N1 results are per-configuration measurements of a
specific stack on specific hardware — useful for comparing kit, not a bar anyone must
clear. (Vendor-published example: AMD's STAC-N1 result summaries. STAC's own report
pages are access-controlled and were not retrievable for this audit; treat any figure
quoted second-hand as unverified.)

What *is* regulated is the **clock**, and — separately — how fast a *surveillance alert*
must fire. Neither is a trading-latency SLA.

| Area | Documented requirement | Source |
|---|---|---|
| EU — HFT clock accuracy | Members/participants using a high frequency algorithmic trading technique: maximum divergence from UTC **100 microseconds**, granularity **1 microsecond or better**. Voice trading systems, RFQ requiring human intervention, and concluding negotiated transactions: **1 second / 1 second or better**. Any other trading activity: **1 millisecond / 1 millisecond or better**. | [Commission Delegated Regulation (EU) 2017/574 (RTS 25), Annex Table 2](https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32017R0574) |
| EU — venue clock accuracy | Gateway-to-gateway latency **> 1 millisecond**: divergence 1 ms, granularity 1 ms or better. Latency **≤ 1 millisecond**: divergence **100 microseconds**, granularity **1 microsecond or better**. | [Reg. (EU) 2017/574 (RTS 25), Annex Table 1](https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32017R0574) |
| EU — real-time alerting | "Real-time alerts shall be generated within five seconds after the relevant event." This is the **only** numeric latency figure in RTS 6. | [Commission Delegated Regulation (EU) 2017/589 (RTS 6), Art. 16(5)](https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32017R0589) |
| EU — surveillance capacity | The automated surveillance system must be able to "read, replay and analyse order and transaction data on an ex-post basis, with sufficient capacity to be able to operate in an automated low-latency trading environment where relevant." | [Reg. (EU) 2017/589 (RTS 6), Art. 13(7)](https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32017R0589) |
| EU — stress testing | Annual self-assessment must include "running high messaging volume tests using the highest number of messages received and sent by the investment firm during the previous six months, multiplied by two". | [Reg. (EU) 2017/589 (RTS 6), Art. 10](https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32017R0589) |

**Consequences for this module.**

1. RTS 25's 100 µs is the tightest clock accuracy any of these regimes demands, and it is
   *twenty times* this module's default 5 µs P50 budget. Two clocks each at the edge of
   that tolerance can contribute up to 200 µs of error to a single interval. A T2T figure
   is only as good as the clocks bracketing it, which is what
   `SLAConfig.timestamp_uncertainty_us` exists to surface. A business clock that merely
   satisfies RTS 25 cannot measure a 2 µs stage at all.
2. RTS 6 Art. 16(5)'s five seconds is a *surveillance alerting* deadline — six orders of
   magnitude looser than a microsecond T2T budget. It is not a trading-latency
   requirement and must not be quoted as one.
3. RTS 6 Art. 10 is the reason to profile T2T **under load**, at twice the highest
   six-month message volume, rather than on a quiet afternoon. A window sampled from a
   quiet market cannot approve an SLA.

## Percentile semantics

| Area | Documented behaviour | Source |
|---|---|---|
| Nearest-rank percentile | `getValueAtPercentile` "Returns the largest value that (100% - percentile) [+/- 1 ulp] of the overall recorded value entries in the histogram are either larger than or equivalent to." | [HdrHistogram `AbstractHistogram` JavaDoc](https://hdrhistogram.github.io/HdrHistogram/JavaDoc/org/HdrHistogram/AbstractHistogram.html) |
| Coordinated omission | "To compensate for the loss of sampled values when a recorded value is larger than the expected interval between value samples, the new histogram will include an auto-generated additional series of decreasingly-smaller ... value records". The at-recording and post-correction methods "are mutually exclusive, and only one of the two should be used on a given data set". | [HdrHistogram `AbstractHistogram` JavaDoc](https://hdrhistogram.github.io/HdrHistogram/JavaDoc/org/HdrHistogram/AbstractHistogram.html) |

The one-ULP nudge is not a detail. `99.9 / 100.0` evaluates to `0.9990000000000001` in
IEEE-754 binary64, so `ceil(0.999... × 1000)` is `1000`, not `999` — which would pin
P99.9 to the observed maximum at exactly the sample count that should first resolve it.
This module reproduces HdrHistogram's `Math.nextAfter(percentile, NEGATIVE_INFINITY)`
guard in `rank_for_percentile` for that reason.

**Coordinated-omission correction is deliberately absent here.** It requires a sampler
with a known fixed cadence; T2T samples are event-driven — one per tick that produced an
order — so there is no expected interval to correct against. The analogous hazard for
this module is a pipeline that *drops* ticks under load and therefore never records its
own worst observations; count drops in the feed handler. For a fixed-cadence series, use
`latency-monitoring-percentile-based-slas`.

## Timestamping and clock domains

| Surface | Documented behaviour | Source |
|---|---|---|
| NIC hardware timestamps | The kernel does not convert them to system time: "ts[1] used to hold hardware timestamps converted to system time. Instead, expose the hardware clock device on the NIC directly as a HW PTP clock source, to allow time conversion in userspace and optionally synchronize system time with a userspace PTP stack". `SOF_TIMESTAMPING_SYS_HARDWARE` "is deprecated and ignored". | [Linux kernel networking timestamping docs](https://docs.kernel.org/networking/timestamping.html) |
| `CLOCK_MONOTONIC_RAW` | "provides access to a raw hardware-based time that is not subject to frequency adjustments" — unlike `CLOCK_MONOTONIC`, which is "affected by frequency adjustments", and `CLOCK_REALTIME`, which is "affected by discontinuous jumps in the system time". | [`clock_gettime(2)`, man7.org](https://man7.org/linux/man-pages/man2/clock_gettime.2.html) |
| Invariant TSC | "Processor's support for invariant TSC is indicated by CPUID.80000007H:EDX[8]." "The invariant TSC will run at a constant rate in all ACPI P-, C-. and T-states." | Intel 64 and IA-32 Architectures Software Developer's Manual, Vol. 3, "Invariant TSC" |

**On the `rdtsc` folklore.** It is commonly said that `rdtsc` "requires CPU core pinning
and disabling C-states / Turbo Boost to ensure constant TSC frequency." On any processor
advertising invariant TSC that is wrong: the counter runs at
a constant rate across all P-, C- and T-states by definition. Disabling deep C-states
remains worthwhile for *wake-up latency* jitter, and core pinning remains worthwhile
because raw TSC readings are only comparable across cores when the TSC is invariant and
synchronised — but neither is what makes the TSC a valid timebase. Confirming the
invariant bit and calibrating the frequency is.

**No vendor accuracy number is encoded in this skill.** Adapter datasheets quote
timestamping *resolution* (single-digit nanoseconds on current Solarflare/AMD and Mellanox
parts). Resolution is not accuracy: end-to-end accuracy against UTC is set by the whole
traceability chain — grandmaster, distribution, path asymmetry, holdover — and must be
measured on the deployed host. A flat "Precision: $< 10\ \text{ns}$" for hardware NIC
timestamping is not a claim any of that supports.
See `hardware-timestamping-vs-software-timestamping-accuracy`.

## This skill's engineering rules

Everything below is an engineering choice made by this skill. **None of it is published
by a regulator, an exchange, or a standards body.**

| Rule | Requirement | Why |
|---|---|---|
| Six capture points | A sample MUST carry $T_0 \dots T_5$. Five stages cannot be derived from five timestamps. | Revision 1.1.0 had five, computed four deltas, labelled each with the following stage's name, and never reported NIC egress. Every optimisation decision taken from such a report aimed one stage too early. |
| One timebase | All six timestamps MUST be converted to a single clock domain before a sample is built. | $T_0/T_5$ are in the NIC PHC domain, $T_1 \dots T_4$ in an in-host counter. A large offset raises; a small one is silently plausible and wrong. |
| Integer nanoseconds | Timestamps MUST be `int`; `float` and `bool` are rejected. | binary64 spacing at epoch-scale nanoseconds is 256 ns, coarser than the stages measured. `bool` subclasses `int`, so `True` would pass as the timestamp `1`. |
| Ordering guard | A stage going backwards MUST reject the whole sample, never be clamped. | It proves the bracketing capture points disagree; the positive stages in the same sample share that error by an unknown amount. |
| Zero-length stages | A 0 ns stage MUST be accepted and reported. | It means the timer could not resolve the stage, not that the stage was free. `below_noise_floor` is how that is surfaced. |
| Estimator | Percentiles MUST default to nearest rank, matching HdrHistogram. | Every reported figure is then a latency actually observed, and reconciles with HdrHistogram-based collectors. Interpolation can report a value the system never produced. |
| Resolution gate | A percentile whose nearest rank equals $N$ MUST NOT support an approval. | That "percentile" is the observed maximum; the window contains no rarer event to measure. |
| Breach/approval asymmetry | A breach MUST be reported at any sample count; an approval MUST NOT. | Observing one over-budget latency proves a breach. Observing none over a short window proves nothing. |
| Tail attribution | A tail MUST be attributed by conditioning on the slow samples, never by summing per-stage percentiles. | The total is the sum of the stages and the mean is linear, so tail-minus-body stage means sum *exactly* to the total excess. Independently ranked per-stage P99s do not, and the error is unsigned. |
| Rank-based tail split | The tail set MUST be chosen by rank, not by `total >= threshold`. | On a pipeline with a flat body the threshold value repeats, and a value-based split puts every sample in the tail — yielding no attribution at all. |
| Integer-ns aggregation | Stage means MUST be taken over integer-ns sums and converted once. | Averaging microsecond floats accumulates rounding; on a flat pipeline the residue is enough to name a `dominant_stage` that does not exist. |
| Sample cap | Exceeding `max_samples` MUST raise, never evict. | Evicting discards observations from the distribution being measured, and the ones a cap drops are the ones that mattered. |
| Empty inputs | An empty sample set and an empty percentile sequence MUST raise, never return `0.0`. | Zero is indistinguishable from a genuinely instant pipeline and reads as a pass. |
| Budget coherence | SLA budgets MUST be finite, non-negative and non-decreasing across P50 → P99 → P99.9 → max. | A tighter budget on a higher percentile can never be satisfied and is a configuration error, not a permanent breach. |

## Tunable defaults (calibrate, do not inherit)

| Parameter | Default | Status |
|---|---|---|
| `max_p50_us` | `5.0` | Engineering starting point. Not published by anyone. |
| `max_p99_us` | `15.0` | Engineering starting point. Not published by anyone. |
| `max_p999_us` | `50.0` | Engineering starting point. Not published by anyone. |
| `max_tail_us` | `100.0` | Engineering starting point. Not published by anyone. |
| `timestamp_uncertainty_us` | `0.0` (off) | Set to the combined uncertainty of the two clocks bracketing a stage. RTS 25's 100 µs per clock is a defensible ceiling for an HFT firm's business clocks — and far too coarse for per-stage work. |
| `percentile_method` | `NEAREST_RANK` | HdrHistogram-compatible. `LINEAR_INTERPOLATION` available for NumPy/Excel parity. |
| `tail_percentile` | `99.0` | Defines the tail set for attribution. Needs ≥ 100 samples to be resolvable. |
| `max_samples` | `None` (unbounded) | Set it if the drain loop could outrun evaluation; exceeding it raises. |

## Scope boundary

This module reads no clock and instruments nothing. It aggregates samples captured
elsewhere, off the hot path, and every guarantee it offers concerns arithmetic over those
samples. It is not a compliance artifact, asserts no regulatory requirement, and its SLA
budgets carry no authority beyond the operator who sets them.

Requires Python 3.10+ for `math.nextafter`.

---

Sources consulted (September 2026): Commission Delegated Regulation (EU) 2017/574 (RTS 25)
Annex Tables 1–2, retrieved from EUR-Lex (CELEX:32017R0574); Commission Delegated
Regulation (EU) 2017/589 (RTS 6) Arts. 10, 13(7) and 16(5), retrieved from EUR-Lex
(CELEX:32017R0589); Linux kernel networking timestamping documentation
(`docs.kernel.org/networking/timestamping.html`); `clock_gettime(2)` man page (man7.org),
via this repo's `latency-monitoring-percentile-based-slas/references/standards.md`;
HdrHistogram `AbstractHistogram` JavaDoc; Intel 64 and IA-32 Architectures Software
Developer's Manual Vol. 3 on invariant TSC and `CPUID.80000007H:EDX[8]`. STAC-N1 was
identified through vendor-published result summaries only — STAC's own documentation
pages returned HTTP 403 and could not be verified directly, so nothing beyond the
benchmark's existence and its mean/P99/max reporting convention is claimed here.
