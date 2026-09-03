# Standards & Sources for Per-Vendor Market Data Latency Monitoring

## There is no published vendor latency SLA to comply with

**No regulator, exchange, standards body or commercial market data vendor publishes a
microsecond latency SLA for a real-time feed.** The `500 µs` P99 budget shipped as this
module's default is an engineering starting point with no external authority behind it.
Engineering opinion in this area is sometimes dressed up in a table headed "Engineering
Standard" using RFC 2119 "MUST" language. That framing implies a source that does not
exist; do not adopt it.

Vendor latency commitments are contractual and negotiated per client, per co-location
site and per product. Bloomberg's B-PIPE and LSEG's real-time feed marketing material
describes low latency without publishing a figure, and no public SLA document for
either could be located.

The one external datapoint on what these SLAs actually look like comes from ESMA, which
surveyed data contributors and reported that respondents "indicated having Service Level
Agreements that range between 1 millisecond and 1 second" ([ESMA MiFIR Review
Consultation Package, ESMA74-2134169708-7225, §40](https://www.esma.europa.eu/sites/default/files/2024-05/ESMA74-2134169708-7225_-_MiFIR_MiFID_Review_-_CP_on_CTPs_and_DRSPs.pdf)).
That is a different population from a co-located HFT feed handler, but it is worth
noting that this module's 500 µs default is **twice as tight as the tightest SLA any
contributor in that survey reported**. Treat it as a placeholder, not a target.

## What *is* regulated

### Clock accuracy — this bounds what you can credibly measure

| Area | Documented requirement | Source |
|---|---|---|
| EU — HFT participants | Members/participants using a high frequency algorithmic trading technique: maximum divergence from UTC **100 µs**, timestamp granularity **1 µs or better**. Any other trading activity: 1 ms / 1 ms. | Commission Delegated Regulation (EU) 2017/574 (RTS 25), Annex Table 2 |
| EU — trading venues | Gateway-to-gateway latency **< 1 ms**: 100 µs divergence, 1 µs granularity. **> 1 ms**: 1 ms / 1 ms. | Commission Delegated Regulation (EU) 2017/574 (RTS 25), Annex Table 1 |

**Consequence for this module.** RTS 25's 100 µs is the tightest clock tolerance any of
these regimes demands, and it is one fifth of this module's 500 µs default budget. Two
clocks each sitting at the edge of that tolerance can contribute up to 200 µs of error
to a single interval. That is what `clock_uncertainty_us` exists to surface: a verdict
inside the noise floor is annotated rather than silently reported as a comfortable pass.

Note that RTS 25 binds trading venues and their members — **not** commercial market data
vendors, which are not parties to it. The `t_vendor_us` stamp in this module's sample
carries no regulated accuracy guarantee at all.

### EU consolidated tape timeliness — a *percentile* obligation, not a maximum

ESMA's final draft RTS under MiFIR Article 22b, Article 3 "Real time transmission of
data to the CTP", requires that data contributors transmit:

> "pre-trade input data to the CTP for shares and ETFs as close to real-time as is
> technically possible and in any case no later than 50 milliseconds after the timestamp
> of the order with a 95% of confidence interval measured on a daily basis"

with the same 50 ms / 95% test for post-trade equity data (from execution for trading
venues, from reception for APAs), and 500 ms for bonds and derivatives.
([ESMA MiFIR Review Final Report on CTPs and DRSPs, ESMA74-2134169708-7768, 16 December
2024](https://www.esma.europa.eu/sites/default/files/2024-12/ESMA74-2134169708-7768_-_MiFIR_review_-_Final_Report_on_CTPs_and_DRSPs.pdf), §31–32 and Annex draft RTS Art. 3.)

Two things matter here:

1. **This obligation is expressed as a percentile with a confidence interval, not as a
   hard maximum.** It is why `audited_percentile` is a constructor argument in this
   module rather than a hard-coded P99. A pipeline whose latency obligation is a P95
   test cannot be audited by a P99-only engine.
2. **It applies to data contributors feeding an EU consolidated tape, at millisecond
   scale.** It is not a latency standard for a commercial low-latency feed, and quoting
   it as one would be a jurisdiction and scope error. It is four to five orders of
   magnitude looser than the budgets this module is normally pointed at.

**Verification limitation.** The adopted instrument is Commission Delegated Regulation
(EU) 2025/1155 (OJ 3 November 2025, in force 23 November 2025, Articles 11–16 applying
from 2 March 2026). EUR-Lex was not reachable from the environment this skill was
audited in, so the figures above are quoted from ESMA's own final report rather than
from the adopted article text. Re-check the adopted wording before relying on the exact
numbers for a compliance purpose.

## Venue timestamp conventions — the epochs do not match

The `t_exchange_us` field assumes a venue timestamp normalised to a common epoch. Venues
do not share one:

| Venue / protocol | Exchange-side timestamp | Source |
|---|---|---|
| Nasdaq TotalView-ITCH 5.0 | A 6-byte integer; "Timestamps are represented as nanoseconds since midnight". No date and no timezone travel in the field. | [Nasdaq TotalView-ITCH 5.0 specification](https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/NQTVITCHSpecification_5.0.pdf), §3 Data Types |
| CME MDP 3.0 | Packet header carries a 4-byte sequence number and an 8-byte sending time — the UTC time the gateway sent the message, in nanoseconds since the Unix epoch. Tag 60 `TransactTime` is likewise nanoseconds since the Unix epoch. | [CME Group Client Systems Wiki, MDP 3.0 SBE Technical Headers](https://cmegroupclientsite.atlassian.net/wiki/spaces/EPICSANDBOX/pages/457638617/MDP+3.0+-+SBE+Technical+Headers) |

Subtracting an ITCH "nanoseconds since midnight" value from a Unix-epoch timestamp
without conversion produces a delta of decades, not a latency. Normalise to one epoch
*before* constructing a `LatencySample`; the module rejects magnitudes that betray an
unconverted nanosecond or wrong-epoch stamp, but it cannot detect a conversion that is
merely wrong by a whole number of hours.

## NIC hardware timestamps are on a different clock from the application

A raw hardware receive timestamp is taken from the NIC's own clock, not the host's
system clock. Linux removed the kernel-side translation between the two:

> "This option is deprecated and ignored."
> — on `SOF_TIMESTAMPING_SYS_HARDWARE`

> "Instead, expose the hardware clock device on the NIC directly as a HW PTP clock
> source, to allow time conversion in userspace and optionally synchronize system time
> with a userspace PTP stack such as linuxptp."

Source: [Linux kernel networking timestamping documentation](https://docs.kernel.org/networking/timestamping.html).

So `t_app_us - t_local_nic_us` mixes two clock domains unless the PTP hardware clock is
disciplined to the system clock (for example with `phc2sys`) or the hardware timestamp
is converted before use. An undisciplined PHC drifting against the system clock produces
a steadily growing — and eventually negative — app-queue segment that has nothing to do
with queueing.

## Percentile semantics

| Area | Documented behaviour | Source |
|---|---|---|
| Nearest-rank percentile | `getValueAtPercentile` "Returns the largest value that (100% - percentile) [+/- 1 ulp] of the overall recorded value entries in the histogram are either larger than or equivalent to." | [HdrHistogram `AbstractHistogram` JavaDoc](https://hdrhistogram.github.io/HdrHistogram/JavaDoc/org/HdrHistogram/AbstractHistogram.html) |

HdrHistogram's one-ULP nudge is reproduced here for the reason it exists: `99.9 / 100.0`
is `0.9990000000000001` in IEEE-754 double precision, so an unguarded
`ceil(0.999... × 1000)` is `1000`, not `999`, which would pin P99.9 to the observed
maximum at exactly the sample count that should first resolve it.

## Float64 resolution — arithmetic, not a citation

A float64 carries a 53-bit significand, so the spacing between representable values
grows with magnitude. For timestamps expressed as microseconds:

| Magnitude | Binade | Spacing | Meaning |
|---|---|---|---|
| `1e12` µs | — | 0.000122 µs | Sub-nanosecond. Fine. |
| `1.8e15` µs (µs since Unix epoch, 2026) | `[2^50, 2^51)` | **0.25 µs** | Sub-microsecond figures are not evidenced. |
| `1e16` µs | `[2^53, 2^54)` | **2 µs** | A microsecond report is fiction. |
| `1e17` µs | `[2^56, 2^57)` | 16 µs | Rejected as a magnitude error. |

A latency is a difference of two timestamps and cannot be finer than the representation
of its operands. `timestamp_quantum_us` reports this spacing on every audit, and a
quantum at or above 1 µs raises a warning. The fix is to rebase against a session epoch
before measuring, not to widen the bound.

## This skill's engineering rules

Everything below is a choice made by this skill. **None of it is published by a
regulator, an exchange, or a vendor.**

| Rule | Requirement | Why |
|---|---|---|
| Negative segment deltas | A negative segment latency MUST reject that vendor's window, not clamp to zero. | A negative duration proves the two clocks bracketing the segment disagree. The positive deltas in the same window carry the same unknown, unsigned error. Clamping produced a healthy 80 µs verdict on a window whose decomposition summed to 2,020 µs. |
| Estimator | Percentiles MUST default to nearest rank, matching HdrHistogram. | Every reported figure is then a latency actually observed, and it reconciles with HdrHistogram-based collectors. Interpolation blends neighbours: on a feed that is either 10 µs or 900 µs it reports a median of 455 µs. |
| Resolution gate | A percentile whose nearest rank equals N MUST NOT support a healthy verdict. | That "percentile" is the observed maximum; the window contains no rarer event. P99 needs 100 samples, P99.9 needs 1,000. |
| Breach/approval asymmetry | A breach MUST be reported at any sample count; a healthy verdict MUST NOT. | Observing one over-budget latency proves a breach. Observing none over a short window proves nothing. |
| Non-finite / bool / non-numeric | MUST be rejected, not filtered. | A NaN does not raise, does not sort and does not compare; it yields an unordered series and an arbitrary median. `True` would be read as 1 µs. |
| Comparison precision | SLA comparisons MUST use unrounded values. | Rounding first turns a 500.004 µs P99 into a 500.00 µs pass. |
| Vendor pooling | A vendor's percentile MUST be computed over its pooled raw samples across symbols and hosts. | A percentile is a quantile of a distribution, not an additive quantity; per-symbol P99s cannot be averaged into a feed P99. |
| Tail attribution | The segment blamed for the tail MUST be identified from the tail subset, not by ranking segment percentiles independently. | Percentiles are not additive. The segment with the highest standalone P99 need not be the one that was slow during the ticks that breached, because those observations can be disjoint. |
| Clock uncertainty | A verdict within the timestamp uncertainty MUST be annotated, never silently widened or narrowed. | The budget is policy; the noise floor is a measurement fact. |
| Jitter | Both σ and IQR MUST be reported. | σ is tail-sensitive, IQR describes the body; one stall separates them by orders of magnitude. |

## Tunable defaults (calibrate, do not inherit)

| Parameter | Default | Status |
|---|---|---|
| `max_allowed_p99_latency_us` | `500.0` | Engineering placeholder. Not published by anyone, and tighter than any SLA in ESMA's contributor survey. |
| `audited_percentile` | `99.0` | Engineering choice. Set it to the percentile your obligation actually attaches to. |
| `percentile_method` | `NEAREST_RANK` | HdrHistogram-compatible. `LINEAR_INTERPOLATION` available for NumPy/Excel parity. |
| `clock_uncertainty_us` | `0.0` (off) | Set to the combined uncertainty of the two clocks bracketing the measurement. RTS 25's 100 µs per clock is a defensible ceiling for your own hosts; a vendor's gateway clock carries no such guarantee. |
| `reject_clock_inconsistent_windows` | `True` | Set `False` only to inspect a known-broken feed, never to make a dashboard green. |

## Scope boundary

This module reads no clock and instruments nothing. It audits timestamps captured
elsewhere and already normalised to a common epoch. Every guarantee it offers is about
arithmetic over those numbers, not about how they were obtained. It is not a compliance
artifact, asserts no regulatory requirement, and its budgets carry no authority beyond
the operator who sets them.

Requires Python 3.9+ for `math.nextafter` and `math.ulp`.
