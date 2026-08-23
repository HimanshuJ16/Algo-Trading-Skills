# Broker Integration Standards — demo-account-realism-gap-assessment

## Scoring parameters

All four values below are **repo conventions and tuning parameters**, not derived,
vendor-mandated, or regulatory figures. They are constructor arguments so they can be
calibrated per strategy; the defaults are starting points, not requirements.

| Metric | Default | Description |
|---|---|---|
| Latency Score Weight | 30% | Ratio of demo vs live execution delay, clamped to [0, 1] |
| Slippage Score Weight | 40% | Exponential decay penalty on the *signed* live-minus-demo adverse slippage gap |
| Fill Rate Weight | 30% | Ratio of live vs demo fill rate, clamped to [0, 1] |
| `slippage_decay_bps` | 10.0 | Gap at which the slippage term scores $e^{-1} \approx 0.368$. Calibrate against the strategy's own edge per trade — a 10 bps gap is fatal to a 12 bps edge and irrelevant to a 200 bps one. |
| `promotion_threshold` | $R \ge 0.75$ | Suggested minimum fidelity for live promotion sign-off |
| `min_samples` | 30 | Below this, results are flagged `is_sample_sufficient=False`. The usual central-limit rule of thumb, not a derived requirement. |

## Slippage sign convention

Slippage is signed by trade direction, per Perold's implementation-shortfall framework:
the arrival (decision) price is the benchmark, a BUY filling above it is a cost, and for
sell orders the price differences reverse — selling below the decision price increases
implementation shortfall while selling above it reduces it. Positive = adverse,
negative = price improvement. Absolute-value slippage is incorrect for this skill's
purpose because it lets demo price improvement cancel live adverse cost.

## Status of the Sharpe discount

$\text{Sharpe}_{\text{adjusted}} = \text{Sharpe}_{\text{demo}} \times R$ is a **heuristic
sizing haircut with no formal literature basis**. It is documented as such deliberately.

- It is applied only when $\text{Sharpe}_{\text{demo}} > 0$; a non-positive Sharpe is
  returned unchanged, because a multiplicative discount would otherwise move a losing
  strategy's Sharpe toward zero.
- It addresses **execution fidelity only**. The recognised statistical correction for
  performance inflation from selection bias, multiple testing, sample length, and
  non-normal returns is the **Deflated Sharpe Ratio** (Bailey & López de Prado, 2014),
  which solves a different problem. Use both; neither substitutes for the other.

## Regulatory context — presenting simulated performance

Demo/paper results are hypothetical performance. In the US futures context, **CFTC
Regulation 4.41(b) (17 CFR 4.41)** requires that a presentation of simulated or
hypothetical performance prominently display a prescribed cautionary statement — either
the language specified in 4.41(b)(1)(i) or, per 4.41(b)(1)(ii), one prescribed by a
registered futures association. Per CFTC staff guidance, all CTAs must comply
"regardless of whether they are required to register," and NFA members must use the
disclaimer in **NFA Compliance Rule 2-29(c)(1)**.

The prescribed CFTC language warns that because the trades were not actually executed,
"the results may have under- or over-compensated for the impact, if any, of certain
market factors, such as lack of liquidity" — which is precisely the gap this skill
quantifies.

**Jurisdiction note:** the above is US futures/CTA regulation. It is cited as context
for why this measurement matters, not as a claim that it governs every user. Other
regimes impose their own restrictions on hypothetical performance (for example, the SEC
Marketing Rule for registered investment advisers); confirm what applies to your entity
and jurisdiction before presenting demo results externally. This module performs no
compliance check of any kind.

## Sources

- CFTC Letter No. 01-60 (Division of Trading and Markets) — scope and mandatory nature of
  Rule 4.41(b) hypothetical-performance disclaimers; NFA Compliance Rule 2-29(c)(1) for
  NFA members. https://www.cftc.gov/sites/default/files/tm/letters/01letters/tm01-60.htm
- Bailey, D. H. & López de Prado, M. (2014), *The Deflated Sharpe Ratio: Correcting for
  Selection Bias, Backtest Overfitting and Non-Normality*.
  https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551
- Perold, A. (1988), *The Implementation Shortfall: Paper versus Reality* — arrival price
  as decision benchmark and the direction-dependent sign of execution cost.
  Overview: https://en.wikipedia.org/wiki/Implementation_shortfall

## Category

`broker-integration` — see top-level `mappings/` directory.
