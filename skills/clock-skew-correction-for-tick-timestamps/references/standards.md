# Standards and Evidence for Clock Skew Correction

## 0. How to read this document

Section 1 is a **regulatory touchpoint**, with the jurisdiction stated. Sections 2–3 are
**published technical evidence**: peer-reviewed measurement literature and the reference
NTP implementation's documented behaviour. Section 4 is **this repository's engineering
guidance** — recommended practice, not a legal requirement, and labelled as such so an
agent does not present it to an operator as a compliance mandate.

Where a number appears in Section 4 it is a default to be calibrated against your own
environment, not a threshold any regulator has set.

## 1. EU / UK — MiFID II RTS 25 (clock synchronisation)

**Instrument:** Commission Delegated Regulation (EU) 2017/574 (RTS 25), supplementing
Directive 2014/65/EU. **Applicability:** trading venue operators, and members or
participants of an EU trading venue. It does not bind a US-only broker-dealer, a
crypto-only operation outside the EU, or an individual trading their own capital. The UK
operates an onshored equivalent supervised by the FCA.

| Provision | Requirement |
|---|---|
| Art. 2 (venue operators) | Gateway-to-gateway latency > 1 ms: max divergence from UTC 1 ms, granularity 1 ms or better. Latency ≤ 1 ms: max divergence 100 µs, granularity 1 µs or better. Voice/manual systems: 1 s divergence, 1 s granularity. |
| Art. 3 + Annex Table 2 (members/participants) | High-frequency algorithmic trading technique: **100 µs** max divergence from UTC, **1 µs or better** granularity. Other algorithmic/electronic trading: 1 ms divergence, 1 ms granularity. Voice, RFQ and negotiated transactions: 1 s / 1 s. |
| Art. 4 | Operators and members must establish a **system of traceability to UTC**, document the design and functioning of their timestamping system, and review compliance at least annually. |

**The point that governs how this skill may be used:** RTS 25 obligations are met by
*synchronising and disciplining the clock* that produces the record, with documented
traceability to UTC. They are **not** met by fitting a regression to captured data after
the fact. Post-hoc skew-corrected timestamps are legitimate for research, reconstruction
and latency analysis; they are not an RTS 25 record and must not be presented as one.
Deploying the clock discipline itself is `clock-synchronization-ptp-for-trading-hosts`;
evidencing ongoing divergence is `clock-drift-monitoring-alerting-thresholds`.

Primary text: <https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32017R0574>.
UK onshored text: <https://www.legislation.gov.uk/eur/2017/574>.

## 2. Measurement literature — what one-way data can and cannot recover

| Source | What it establishes |
|---|---|
| V. Paxson, *On calibrating measurements of packet transit times*, ACM SIGMETRICS '98, pp. 11–21. | The segment-minima approach this skill follows: divide the trace into intervals, take the least-delayed samples in each as the ones least corrupted by queueing, and estimate the relative clock rate from them. Note that Paxson's full procedure uses **forward and reverse** path measurements; a market data feed gives only the forward direction, so only the one-way part of the method applies here. |
| S. B. Moon, P. Skelly, D. Towsley, *Estimation and removal of clock skew from network delay measurements*, IEEE INFOCOM '99, pp. 227–234. <http://projectsweb.cs.washington.edu/research/projects/networking/www/detour/local/infocom99/papers/02b_03.pdf> | Three results this skill depends on. (a) Measured one-way delay decomposes into a fixed propagation component, a variable positive queueing component, and the relative clock offset — and **the fixed delay and the constant clock offset are not separable from one-way measurements**; only the *skew* (the slope) is identifiable. (b) Ordinary least squares over all delay samples is biased because queueing error is one-sided and positive. (c) Their linear-programming estimator fits the lower envelope directly, runs in O(N), and leaves post-correction delays positive. |

**Consequence for this implementation.** `ClockSkewCorrector.alpha` is
`minimum_one_way_transit + clock_offset_at_reference`. It is reported as a constant term,
never as a clock offset. The per-window-minimum regression used here is the cheaper,
widely used approximation to the Moon et al. envelope fit: the window minima still sit
*above* the true lower envelope by an amount that shrinks with samples per window, which
is why sparse windows are excluded and why `diagnostics.residual_std_sec` is exposed. A
caller needing an unbiased envelope should use the linear-programming formulation.

## 3. NTP clock discipline — documented behaviour

From the reference NTP implementation's clock discipline documentation,
<https://www.ntp.org/documentation/4.2.8-series/clock/>:

| Parameter | Documented value | Why it matters here |
|---|---|---|
| Step threshold | 128 ms by default | Below it the daemon *slews*; above it the clock is **stepped**. A step is a discontinuity in the delay series, not drift, and a single line fitted across one is wrong on both sides. |
| Slew rate | Fixed at 500 ppm by the Unix kernel | The maximum *sustained* rate offset a disciplined clock can exhibit while being corrected. It is the basis for this skill's plausibility ceiling. |
| Panic threshold | 1000 s by default | Beyond it the daemon exits and requires manual intervention; a capture spanning such an event is not repairable by regression. |

Leap seconds are a separate 1 s discontinuity of the same shape. The last positive leap
second was inserted on 31 December 2016, and the 27th CGPM resolved in November 2022 to
stop inserting them by 2035 — so historical captures before 2017 may contain one, while
future captures are not expected to.

## 4. Engineering guidance (this repository, not a regulatory requirement)

| Item | Guidance | Reasoning |
|---|---|---|
| Regression input | Fit the **minimum** delay per window, never the mean or median. If a robustified variant is wanted, a low percentile (e.g. 5th) within each window trades a little bias for resilience to a spuriously early timestamp — but it is a variant, not the standard. | Section 2: queueing error is one-sided. |
| Sparse windows | Exclude windows below `min_points_per_window` (default 10). | The expected minimum of *k* draws falls with *k*, so density variation becomes fake skew. |
| Plausibility ceiling | Reject a fitted \|skew\| above `max_drift_ppm` (default **1000 ppm**) rather than applying it. | Two times the 500 ppm kernel slew ceiling in Section 3. A larger sustained rate is a data defect — mixed units, mixed hosts, or a step — not a clock. |
| Time representation | **int64 nanoseconds** for tick data. | Float64 has a ULP of ~238 ns near 1.7e9 s, so float64 POSIX seconds cannot represent nanosecond timestamps at all, and a sub-microsecond monotonicity epsilon added to one is a silent no-op. |
| Monotonicity | Corrected timestamps must satisfy `T_i > T_{i-1}`, and the enforcement must be *verified in the output representation*, not merely attempted. | A guarantee that silently fails is worse than none: downstream reconstruction and sequence matching assume it. |
| Causality | Calibrate on data strictly earlier than the ticks being corrected whenever the output feeds research or a strategy. | `fit_transform` over a whole session stamps early ticks using later ones — look-ahead. |
| Corroboration | Cross-check a material fitted drift against host clock telemetry (`chronyc tracking`, `ptp4l`/`pmc` offsets) before acting on it. | Section 2(a): from one-way data a drifting path and a drifting clock are the same observation. |
