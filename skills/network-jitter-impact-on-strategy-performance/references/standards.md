# Standards & Sources for Network Jitter Impact on Strategy Performance

## There is no published jitter-to-Sharpe relationship

**No regulator, exchange, standards body, vendor or paper publishes a coefficient
relating network delay variation to Sharpe ratio.** The `SR(σ) = SR_base − γσ` model in
this skill is an engineering heuristic, and `base_sharpe = 2.5`,
`jitter_penalty_coeff = 0.5`, `target_sharpe_min = 1.0` and
`max_acceptable_jitter_ms = 3.0` are placeholders that let the module run, not
requirements. Presenting the formula in a "MUST" table of "Engineering Standards" would
dress a fitted heuristic as a published standard.

γ is dimensioned — **Sharpe lost per millisecond of one-way delay standard deviation** —
and is specific to a strategy, venue, instrument and network path. Obtain it by
regressing the strategy's own realized Sharpe on measured jitter across comparable
windows. There is no substitute and no default worth inheriting.

### What the peer-reviewed literature actually says

| Claim | Source | What it supports here |
|---|---|---|
| The cost of latency, as a fraction of the cost of immediacy, is asymptotically $\frac{\sigma\sqrt{\Delta t}}{\delta}\sqrt{\frac{\log 2}{2\pi}} + o(\sqrt{\Delta t})$ as $\Delta t \to 0$, for price volatility $\sigma$ and bid-offer spread $\delta$. "When latency is low, there are increasing marginal benefits to further reductions in latency, i.e. $LC''(\Delta t) < 0$." | Moallemi & Saglam, "OR Forum — The Cost of Latency in High-Frequency Trading", *Operations Research* 61(5), 2013, pp. 1070–1086, Corollary 1 ([author copy](https://moallemi.com/ciamac/papers/latency-2009.pdf), [INFORMS](https://pubsonline.informs.org/doi/10.1287/opre.2013.1165)) | The published relationship between delay and trading cost is **concave**, not linear. This skill's linear penalty is a local approximation only, valid over the range γ was fitted on. Note the result concerns *mean* latency $\Delta t$, not delay variation, so it bounds the shape of the relationship rather than supplying γ. |
| Latency cost "is of the same order of magnitude as other trading costs (e.g., commissions...)". | Moallemi & Saglam (2013), §1 | Latency effects are material at the same scale as fees, which is why an explicit budget is worth maintaining — not a licence to quantify them with an unfitted coefficient. |
| "In the modal race, the winner beats the first loser by just 5–10 microseconds." About 4% of the time the winner's message "actually arrives to the exchange slightly later than the first loser's message, but nevertheless gets processed first." | Aquilina, Budish & O'Neill, "Quantifying the High-Frequency Trading 'Arms Race'", *Quarterly Journal of Economics* 137(1), 2022, pp. 493–564, §I ([author copy](https://ericbudish.org/wp-content/uploads/2022/02/Quantifying-the-High-Frequency-Trading-Arms-Race.pdf)) | The margin that decides a latency race is microseconds. A millisecond-scale jitter budget is three orders of magnitude too coarse for a strategy competing in races. The 4% reordering figure is also direct evidence that the *exchange's own* processing contributes jitter you cannot engineer away. |
| "Price impact from trading in races is about 31% of all price impact and about 33% of the effective spread." The latency arbitrage tax "is 0.42 basis points if using total trading volume, and 0.53 basis points if using only trading volume that takes place outside of races"; the average value-weighted effective spread is "just over 3 basis points". | Aquilina, Budish & O'Neill (2022), §I | Quantifies the adverse-selection channel this skill's premise rests on. **Jurisdiction/scope: FTSE 100 symbols on the London Stock Exchange**, from exchange message data. Do not present these figures as universal across venues or asset classes. |
| "About 22% of trading volume and 21% of trades are in races" for the FTSE 100; "the average FTSE 100 symbol has 537 latency arbitrage races per day". | Aquilina, Budish & O'Neill (2022), §I | Same scope caveat. |

## Jitter is three different quantities

| Metric | Definition | Source |
|---|---|---|
| **σ of one-way delay** — what this module reports as `jitter_std_ms` | Bessel-corrected (n−1) standard deviation of the one-way delays in the window. Not defined by any IETF RFC; it is the ordinary sample statistic. | This module. |
| **PDV** (Packet Delay Variation) — `pdv_p99_ms` | "PDV(i) = D(i)−D(min)", where the reference packet is "the Type-P packet within the specified interval with the minimum one-way delay". | [RFC 5481 §4.2](https://www.rfc-editor.org/rfc/rfc5481.txt) |
| **IPDV** (Inter-Packet Delay Variation) | "IPDV(i) = D(i)-D(i-1) where D(i) denotes the one-way delay of the ith packet of a stream." | [RFC 5481 §4.1](https://www.rfc-editor.org/rfc/rfc5481.txt), [RFC 3393](https://www.rfc-editor.org/rfc/rfc3393.txt) |
| **RFC 3550 interarrival jitter** | A smoothed exponential average, `J = J + (|D(i-1,i)| - J)/16`, over the difference in relative transit times of *consecutive* packets. | [RFC 3550 §6.4.1, §A.8](https://www.rfc-editor.org/rfc/rfc3550.txt) |

These do not agree numerically and answer different questions. RFC 5481 §7.1.4 notes that
PDV is the preferred form for service level agreements ("one constraint needed for
single-sided distribution"), and §7.1.2 that anchoring at the minimum is what makes the
range meaningful for de-jitter buffer sizing. This module reports PDV at the tail
alongside σ for that reason: the minimum is proof of what the path is capable of, so
`P99 − min` is the honest statement of how far behind its own best case a tail packet
falls.

RFC 5481 §7.2.1 notes IPDV is preferred "when measurement clocks exhibit some skew" —
worth knowing if your two clocks are poorly disciplined, since IPDV differences a
common skew and PDV does not.

## Percentile semantics

| Area | Documented behaviour | Source |
|---|---|---|
| Nearest-rank percentile | `getValueAtPercentile` "Returns the largest value that (100% - percentile) [+/- 1 ulp] of the overall recorded value entries in the histogram are either larger than or equivalent to." Implemented as `countAtPercentile = ceil(requestedPercentile * totalCount / 100)`, where `requestedPercentile` is `Math.nextAfter(percentile, Double.NEGATIVE_INFINITY)`. | [HdrHistogram `AbstractHistogram` JavaDoc](https://hdrhistogram.github.io/HdrHistogram/JavaDoc/org/HdrHistogram/AbstractHistogram.html) |

This module reproduces that rank rule, including the one-ULP nudge, so its percentiles
reconcile with HdrHistogram-based collectors and with the sibling skill
`latency-monitoring-percentile-based-slas`. Requires Python 3.10+ for `math.nextafter`.

## The clock is the binding constraint on how tight a budget you can claim

A one-way delay spans two hosts, so it cannot be measured with a monotonic clock; it
needs synchronised real-time clocks, and the measurement's error bar is the two clocks'
combined divergence.

| Area | Documented requirement | Source |
|---|---|---|
| EU — HFT clock accuracy | For members/participants using a high frequency algorithmic trading technique, maximum divergence from UTC is **100 µs** and timestamp granularity **1 µs or better**. Other trading activity: 1 ms / 1 ms. | Commission Delegated Regulation (EU) 2017/574 (RTS 25), Annex Table 2 |
| EU — venue clock accuracy | Trading venues with a gateway-to-gateway latency **≤ 1 ms** must hold 100 µs divergence and 1 µs granularity; **> 1 ms**, 1 ms and 1 ms. | Commission Delegated Regulation (EU) 2017/574 (RTS 25), Annex Table 1 |
| US — CAT clock sync | Industry Member Business Clocks (other than those used solely for Manual Order Events or allocation time) must be synchronised "at a minimum to within a fifty (50) millisecond tolerance" of NIST. | [FINRA Rule 6820](https://www.finra.org/rules-guidance/rulebooks/finra-rules/6820) |

**Consequence.** Two clocks each at the edge of the RTS 25 100 µs tolerance can
contribute up to 200 µs of error to a single one-way delay. A jitter budget tighter than
that is not measurable by those clocks, whatever the dashboard says — and the CAT 50 ms
tolerance is a *recordkeeping* requirement four orders of magnitude looser than that, so
clocks that merely satisfy FINRA Rule 6820 cannot measure sub-millisecond jitter at all.

**These clock rules govern the timestamps, not the jitter.** Neither RTS 25 nor FINRA
Rule 6820 imposes any network jitter limit. Nothing in this module is a compliance
artifact.

## This skill's engineering rules

Everything below is a choice made by this skill. **None of it is published by a
regulator, an exchange, or a standards body.**

| Rule | Requirement | Why |
|---|---|---|
| Estimator | Percentiles MUST use nearest rank, `ceil(p/100 × N)`. | Every reported figure is then a delay actually observed, and reconciles with HdrHistogram collectors. The v1.0.0 `int(n × p)` index was one rank too high everywhere. |
| Resolution gate | A percentile whose nearest rank equals N MUST NOT support an approval. | That "percentile" is the observed maximum; the window holds no rarer event. P99 needs 100 packets. |
| Breach/approval asymmetry | A breach MUST be reported at any packet count; an approval MUST NOT. | Observing one over-budget delay proves a breach. Observing none over a short window proves nothing. |
| Minimum window | A single-packet window MUST be rejected. | Its σ is zero by construction and would read as a perfectly jitter-free link. |
| Non-finite samples | NaN/Inf MUST be rejected, not filtered. | NaN breaks `sorted()` silently and compares `False` against every budget, so a corrupted capture reads as a pass. |
| Negative delays | A negative one-way delay MUST reject the whole window. | It proves the bracketing clocks disagree; the positive delays share that error by an unknown amount. |
| Comparison precision | Budget comparisons MUST use unrounded values. | Rounding first turns a 5.0004 ms P99 into a 5.00 ms pass. |
| Clamp ordering | The Sharpe floor MUST be tested on the unclamped modelled value. | A negative `target_sharpe_min` would otherwise be satisfied by the presentational `max(0.0, ·)` clamp. |
| Absolute ceiling | `max_acceptable_jitter_ms` MUST be enforced independently of the Sharpe model. | A generous γ must not be able to approve a link separately judged too variable. Declared but never read in v1.0.0. |
| Model provenance | Every report MUST record which degradation model produced its Sharpe figure. | So a modelled number is never mistaken downstream for a measured one. |
| Variation metrics | σ, IQR and PDV MUST all be reported. | σ is tail-sensitive, IQR describes the body, PDV anchors on the link's demonstrated best case. One stall separates them by orders of magnitude, and that divergence is itself the diagnosis. |

## Tunable defaults (calibrate, do not inherit)

| Parameter | Default | Status |
|---|---|---|
| `base_sharpe` | `2.5` | Placeholder. Not published by anyone. |
| `jitter_penalty_coeff` (γ) | `0.5` Sharpe/ms | Placeholder. **Must be regressed from your own realized Sharpe.** |
| `target_sharpe_min` | `1.0` | Placeholder; a policy choice about when a strategy stops being worth running. |
| `max_acceptable_jitter_ms` | `3.0` ms | Placeholder. Coincides with the default Sharpe-derived tolerance `(2.5 − 1.0) / 0.5`, so enforcing it changes no default-configuration verdict. |
| `max_p99_latency_ms` | `None` (off) | Opt-in. An uncalibrated tail budget produces confident nonsense. |

For a strategy competing in latency-arbitrage races, every millisecond figure above is
the wrong order of magnitude — see the 5–10 µs modal race margin in the table at the top.

## Scope boundary

This module reads no clock and instruments nothing. It audits a delay series captured
elsewhere, and every guarantee it offers concerns arithmetic over those samples. Its
Sharpe output is a model estimate under an operator-supplied coefficient, never a
measurement of realized PnL. It is not a compliance artifact, asserts no regulatory
requirement, and its budgets carry no authority beyond the operator who sets them.
