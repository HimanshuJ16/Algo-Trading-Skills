# Standards and Sources for Strategy Latency Budget Decomposition

## What nobody publishes

**No regulator, exchange, vendor or standards body publishes a per-stage tick-to-trade
latency budget.** A previous revision of this skill presented the table below under the
heading "Standard Microsecond Budget SLA", which implied an authority that does not
exist. Latency is a competitive parameter, not a regulated one: venues publish their own
gateway and matching-engine characteristics, and firms derive their internal budgets from
the opportunity they are trading. Treat any per-stage figure — including the shipped
defaults — as an operator-chosen allocation that must be justified locally.

| Pipeline stage | Shipped default allocation | Status |
|---|---|---|
| `INGRESS_NETWORK` | 2.0 µs | Illustrative placeholder |
| `MARKET_DATA_DECODE` | 3.0 µs | Illustrative placeholder |
| `SIGNAL_COMPUTATION` | 10.0 µs | Illustrative placeholder |
| `PRE_TRADE_RISK` | 5.0 µs | Illustrative placeholder |
| `EGRESS_ORDER_ENCODE` | 5.0 µs | Illustrative placeholder |
| **Total** | **25.0 µs** | Illustrative placeholder |

These exist so the module runs out of the box. They are not a recommendation, and they
are not benchmarked against any published pipeline.

## Calibrating the total budget

The end-to-end budget comes from the opportunity, not from the hardware. The most
directly relevant published measurement of how much time an opportunity actually allows:

- **Aquilina, M., Budish, E., and O'Neill, P. (2022), "Quantifying the High-Frequency
  Trading 'Arms Race'", *The Quarterly Journal of Economics* 137(1), 493–564.**
  A message-level study of FTSE 100 order books. The modal latency-arbitrage race is
  decided in **5–10 microseconds**. A strategy competing in those races has a
  single-digit-microsecond end-to-end budget; a strategy capturing a slower signal has
  more. Nothing in this module infers which case applies to you.
  <https://academic.oup.com/qje/article/137/1/493/6368348>

## Why per-stage percentiles must not be summed

Sizing each stage at its own P99 and adding the results is exact only when the stages are
*comonotonic* — when they move to their tails together, which is what a garbage
collection pause, a scheduler preemption or an interrupt storm does to a whole pipeline.
Quantiles are additive under comonotonic dependence; away from it, the quantile
functional is not subadditive and the direction of the error is not guaranteed.

- **McNeil, A. J., Frey, R., and Embrechts, P. (2015), *Quantitative Risk Management:
  Concepts, Techniques and Tools*, revised edition, Princeton University Press.**
  Contains the standard counterexample in which the Value-at-Risk of a portfolio of
  *independent* defaultable bonds exceeds the sum of the individual VaRs. The structure
  is the same one a latency pipeline exhibits: independent positions with rare, skewed
  tail events. <https://press.princeton.edu/books/hardcover/9780691166278/quantitative-risk-management>
- **Embrechts, P., Nešlehová, J., and Wüthrich, M. V. (2009), "Additivity properties for
  Value-at-Risk under Archimedean dependence and heavy-tailedness", *Insurance:
  Mathematics and Economics* 44(2), 164–169.** Shows VaR turning asymptotically
  superadditive once the marginal regular-variation index falls below one, and
  subadditive above it — i.e. the sign of the additivity error is a property of the
  marginals, not only of the dependence.
  <https://www.sciencedirect.com/science/article/abs/pii/S0167668708000991>

Applied to this module: `sum_of_stage_p99_us` is what stage-by-stage budgeting predicts,
`p99_total_us` is what was measured, and `comonotonic_gap_us` is their signed difference.
Only the measured total is a statement about the end-to-end tail. The worked case in the
test suite — five stages each stalling on a different 1% of 100 traces — gives a measured
total P99 of 19.0 µs against a sum-of-stage-P99s of 11.0 µs.

## Why one stage may not be worth optimising

- **Amdahl, G. M. (1967), "Validity of the single processor approach to achieving large
  scale computing capabilities", *AFIPS Spring Joint Computer Conference*, vol. 30,
  483–485.** The bound the module applies per stage: the most that optimising one
  component can remove from the total is that component's own contribution. Hence
  `stage_reduction_required_fraction = deficit / stage_latency`, and a value above 1.0
  means the stage cannot close the deficit even if eliminated.
  <https://dl.acm.org/doi/10.1145/1465482.1465560>

## Percentile convention

Percentiles are computed by **nearest rank**, `ceil(p/100 × N)` into the ascending-sorted
series, matching HdrHistogram's `getValueAtPercentile` and the sibling skills
`latency-monitoring-percentile-based-slas` and
`network-jitter-impact-on-strategy-performance`. Every reported percentile is a value that
was actually observed; linear interpolation (the NumPy and Excel default) would report a
latency the pipeline never produced. A percentile whose nearest rank lands on the last
sample is the observed maximum, which is why an approval requires at least 100 traces
before a P99 is treated as resolved.

## Regulatory touchpoint — timestamping, not latency

**Commission Delegated Regulation (EU) 2017/574 (MiFID II RTS 25)** — regulatory
technical standards on the level of accuracy of business clocks.

| Who | Maximum divergence from UTC | Timestamp granularity |
|---|---|---|
| Members/participants using a high-frequency algorithmic trading technique (Annex, Table 2) | 100 µs | 1 µs or better |
| Trading venues with gateway-to-gateway latency under 1 ms (Annex, Table 1) | 100 µs | 1 µs or better |
| Trading venues with gateway-to-gateway latency over 1 ms (Annex, Table 1) | 1 ms | 1 ms or better |

Source: <https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32017R0574>

Two consequences, both of which this skill depends on:

1. **RTS 25 is not a latency SLA.** It constrains how accurately a *reportable event* is
   timestamped against UTC. It says nothing about how fast a pipeline may be, and
   compliance with it is not evidence that any latency budget is met.
2. **An RTS 25-compliant business clock cannot measure a microsecond stage budget.** A
   clock permitted 100 µs of divergence and 1 µs of granularity cannot resolve a 2 µs
   stage. Per-stage durations must come from an in-host monotonic counter or NIC hardware
   timestamps; the RTS 25 clock is a separate instrument solving a separate problem.
   See `clock-synchronization-ptp-for-trading-hosts` and
   `hardware-timestamping-vs-software-timestamping-accuracy`.

Jurisdiction: RTS 25 applies to EU trading venues and their members/participants. It was
assimilated into UK law after EU exit; confirm the current UK text separately rather than
assuming the EU version applies unchanged.
