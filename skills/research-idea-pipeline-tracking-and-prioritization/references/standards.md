# Standards — research-idea-pipeline-tracking-and-prioritization

## Configuration defaults (calibrate before use)

These are the engine's defaults. They are **not** industry standards, **not** regulatory
minimums, and not derived from any published source. No regulator, exchange, or standards
body publishes a minimum expected Sharpe for research triage, a minimum capacity for a
research candidate, or a research review cadence. The right values depend on the firm's
size, the strategies it runs, and how much researcher time it has. Calibrate each and
record the values alongside the report — a threshold-dependent ranking is not reproducible
without them.

| Parameter | Default | What it actually does |
|---|---|---|
| `min_priority_score` | $1.0$ | Ideas scoring below this are flagged `below_priority_threshold` and counted in `below_threshold_count`. They are **still ranked and returned** — nothing is dropped. |
| `top_n` | $5$ | Length of the `top_priority_ideas` shortlist. `ranked_ideas` always holds the full active backlog. |
| `max_stage_age_days` | $30.0$ | Days since the last stage change after which a non-terminal idea is reported in `stalled_ideas` and logged as a warning. Terminal stages (`PRODUCTION_READY`, `REJECTED`) never stall. |
| `MIN_TIER` / `MAX_TIER` | $1$ / $5$ | Inclusive bounds on `implementation_complexity` and `data_cost_tier`. Out-of-range values raise; they are **not** clamped. |
| `MIN_CAPACITY_USD` | $\$1$ | Lower bound of the score's domain. $\log_{10}$ is negative below $\$1$, which inverts the sign of the whole score. |

## The priority score is a house heuristic

$$\text{priority} = \frac{S \times \log_{10}(C_{\text{USD}})}{k \times d}$$

There is no external authority for this formula, and this file does not claim one. It
exists to make a backlog ordering explicit and arguable rather than implicit and personal.
Four properties are worth stating before anyone quotes a number from it:

| Property | Consequence |
|---|---|
| The capacity term is $\log_{10}$ of a **dimensional** quantity — really $\log_{10}(C/\$1)$ | Changing the unit shifts each score by $S/(kd)$ per decade, a *different* amount per idea, so the **ranking reorders**. Capacity must be in whole US dollars. |
| The score is **ordinal** | A score of 8 is not twice as good as 4. Do not average scores, do not budget against them, do not compare across registers. |
| $S < 0$ inverts monotonicity in $k$ | $-2.0 \times 7 / 1 = -14.0$ but $-2.0 \times 7 / 5 = -2.8$: the *harder* idea would rank *better*. Negative Sharpe is rejected at registration. |
| $k$ and $d$ are **ordinal tiers multiplied as if cardinal** | A tier-4 idea is not literally twice the work of a tier-2 idea. The denominator is a ranking device, not an estimate. |

## Quantitative context (verified against primary sources)

The engine's arithmetic is trivial; the risk lives in `expected_sharpe`, which is an
assertion by the proposer. These sources bound how much that assertion can be trusted.

| Fact | Source | Applied here |
|---|---|---|
| Given the multiple testing behind hundreds of published factors, a newly discovered factor should clear a t-ratio above roughly **3.0**, not the conventional 2.0 | Harvey, C.R., Liu, Y. and Zhu, H. (2016), "… and the Cross-Section of Expected Returns", *Review of Financial Studies* 29(1), 5–68 — [Oxford Academic](https://academic.oup.com/rfs/article-abstract/29/1/5/1843824), [NBER w20592](https://www.nber.org/papers/w20592) | The register ranks on `expected_sharpe`. An unadjusted best-of-N backtest Sharpe carries a selection bias larger than any ordering difference this score resolves. Deflate before entering, or record that you did not. |
| The Deflated Sharpe Ratio corrects an observed Sharpe for selection bias, backtest overfitting, sample length and non-normality when many trials were evaluated | Bailey, D.H. and López de Prado, M. (2014), "The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and Non-Normality", *Journal of Portfolio Management* 40(5), 94–107 — [SSRN 2460551](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551) | The recommended adjustment to apply to `expected_sharpe` before registration. See `factor-research-multiple-testing-correction`. |
| The $\sqrt{T}$ annualisation of a Sharpe ratio holds only "under simple conditions with zero serial correlation" | Sharpe, W.F. (1994), "The Sharpe Ratio", *Journal of Portfolio Management* 21(1) — [author's text](https://web.stanford.edu/~wfsharpe/art/sr/sr.htm) | Every `expected_sharpe` in one register must use the **same** horizon and annualisation convention. A daily 0.15 ranked against an annualised 2.4 is a $\sqrt{252}$ error. |

## Regulatory position (read this before citing anything)

**No regulation requires a research idea register, a prioritisation score, or a research
review cadence.** This skill is a management tool, not a compliance control, and nothing in
it should be presented to a regulator as satisfying an obligation.

The obligations that *do* exist attach later in the lifecycle — to the development,
testing, approval and deployment of an algorithm that will trade — and are covered by the
skills that own them:

- `strategy-research-to-production-pipeline-governance` — approval and promotion gates.
- `mifid-ii-algo-trading-compliance-eu`, `uk-fca-algorithmic-trading-systems-controls`,
  `sec-rule-15c3-5-risk-controls-us` — the jurisdiction-specific requirements.
- `backtest-audit-trail-for-regulatory-review` — evidence retention for backtests.

The transition log this engine keeps (`get_history`) is a research management record. It is
not a substitute for any of the above, and the engine does not persist it anywhere.

## Known limitations

- **The engine validates ranges, not truthfulness.** `expected_sharpe` and
  `estimated_capacity_usd` are unverifiable claims; the score inherits their optimism.
- **No overlap or correlation modelling.** Two descriptions of the same trade both rank
  highly — see `cross-strategy-correlation-monitoring`.
- **Staleness measures time since the last stage change**, not time since the last work
  done. Active research that does not change stage reads as stalled.
- **No persistence.** State lives in the engine instance for the life of the process; the
  register and its history are lost on exit unless the caller serialises them.
- **No researcher-capacity model.** The score ranks ideas; it does not know who is free,
  what they are good at, or how long anything will take.

## Category

`backtesting-methodology` — see top-level `mappings/` directory.
