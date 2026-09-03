# Institutional Standards — monte-carlo-strategy-robustness-testing

| Simulation Mode | Sampling Method | What it measures | What it cannot measure | Sign-off criterion |
|---|---|---|---|---|
| Sequence Shuffling | Permutation, without replacement | Path/ordering risk: how deep the drawdown could have been had the same trades arrived in a different order | Terminal-wealth dispersion (invariant under permutation); serial dependence between trades | $DD_{95} \le$ limit and breach probability $\le$ ceiling |
| Bootstrap Resampling | With replacement (Efron 1979) | Sampling variation in both drawdown and terminal wealth, with a deeper tail than shuffling | Losses larger than the worst observed trade; serial dependence | $DD_{95} \le$ limit and breach probability $\le$ ceiling |
| Execution Noise Injection | Per-trade $\mathcal{N}(\mu_{\text{cost}}, \sigma_{\text{noise}})$, order preserved | Sensitivity of the edge to fills worse than the backtest assumed | Anything about ordering or resampling; market-impact feedback | Median terminal equity $>$ initial capital under a **negative** $\mu_{\text{cost}}$ |

Default thresholds in the reference implementation — a $25\%$ drawdown limit and a
$1.0\%$ breach-probability ceiling — are **house risk-appetite parameters**, not
standards, and are exposed as constructor arguments precisely so they are not
mistaken for fixed rules. Calibrate them from the desk's own capital and mandate
(`risk-limit-calibration-against-historical-drawdowns`).

## Terminology

"Risk of Ruin" is used loosely across the retail trading literature. This skill's
`risk_of_ruin_pct` is $P(DD_{\max} \ge \text{limit})$ — the probability that a
simulated path breaches a chosen drawdown limit. Classical risk of ruin is the
gambler's-ruin probability of the account reaching an absorbing barrier at or near
zero capital. The two coincide only when the limit is set at total loss. The field
name is retained for API compatibility; do not report the number to a risk
committee as the probability of losing the account.

## Method Assumptions and Their Failure Mode

Both resampling modes assume trade returns are **exchangeable** — that any ordering
of the observed trades is equally likely. Real strategies violate this routinely:
trend-following clusters wins and losses by regime, and position sizing that reacts
to recent performance induces serial dependence by construction. Under violation,
resampling breaks up the loss clusters that generate the deepest real drawdowns, so
the reported $DD_{95}$ is biased **optimistically**. For dependent return streams,
a block bootstrap that resamples contiguous runs of trades is the appropriate
method; see the sources below and `synthetic-data-generation-for-backtest-augmentation`.

## Quantile Estimation

Drawdown quantiles use the nearest-rank (inverse-CDF) estimator
$\hat{Q}(q) = x_{(\lceil qM \rceil)}$ over the $M$ sorted path outcomes — no
interpolation, so every reported figure is a drawdown some path actually realized.

Sample size follows from the sampling error of that order statistic: the count of
paths below the true $q$-quantile is $\text{Binomial}(M, q)$ with standard
deviation $\sqrt{M q (1-q)}$. At $q = 0.95$ this is $\pm 2.2$ ranks out of $100$
paths ($2.2\%$ of the distribution) but $\pm 6.9$ out of $1{,}000$ ($0.69\%$),
which is the basis for the $M \ge 500$ floor.

## Sources

Method choices rest on the following published results. Bibliographic details were
checked against the primary record.

| Claim | Source | Status |
|---|---|---|
| Resampling with replacement from the observed sample estimates the sampling distribution of a statistic (the bootstrap) | Efron, B. (1979), "Bootstrap Methods: Another Look at the Jackknife", *The Annals of Statistics* 7(1), 1–26. https://doi.org/10.1214/aos/1176344552 | Verified; the basis for `run_bootstrap_resampling` |
| The IID bootstrap is invalid under serial dependence; resampling contiguous blocks preserves the short-range dependence structure | Künsch, H. R. (1989), "The Jackknife and the Bootstrap for General Stationary Observations", *The Annals of Statistics* 17(3), 1217–1241. https://doi.org/10.1214/aos/1176347265 | Verified; the basis for the exchangeability caveat and the block-bootstrap recommendation |
| Block bootstrap with geometrically distributed block lengths, yielding a stationary resampled series | Politis, D. N. & Romano, J. P. (1994), "The Stationary Bootstrap", *Journal of the American Statistical Association* 89(428), 1303–1313. https://doi.org/10.1080/01621459.1994.10476870 | Verified; cited as the appropriate alternative for dependent trade streams, not implemented here |

## Regulatory & Operational Notes

**No regulator mandates Monte Carlo resampling of a strategy's trade log.** Nothing
in this skill is a compliance control. Two adjacent surfaces are worth naming
precisely, with their jurisdiction attached:

- **EU/EEA — MiFID II RTS 6** (Commission Delegated Regulation (EU) 2017/589,
  19 July 2016). Article 5 requires investment firms engaged in algorithmic trading
  to establish documented methodologies for developing and testing algorithms
  before deployment or substantial update, including that the algorithm continues
  to work effectively under stressed market conditions. Pre-deployment robustness
  testing of this kind is one way to evidence that methodology; the article does not
  prescribe Monte Carlo specifically. Note that RTS 6 Article 10 ("stress testing")
  concerns the *systems'* ability to withstand increased order flow and market
  stress as part of the annual self-assessment — an operational capacity test, not
  a strategy P&L simulation. Do not cite Article 10 for this skill.
  https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng

- **US — model risk management.** Federal Reserve SR 11-7 / OCC Bulletin 2011-12
  (April 2011) was the long-standing reference for model development, validation,
  and governance. **It has been rescinded.** On 17 April 2026 the Federal Reserve
  issued SR 26-2, "Revised Guidance on Model Risk Management", replacing SR 11-7
  (and SR 21-8); the OCC issued the parallel Bulletin 2026-13, which rescinds
  OCC Bulletin 2011-12. The revised guidance takes a risk-based approach scaled to
  an institution's profile and is stated to be most relevant to Fed-supervised
  banking organizations with over \$30 billion in total assets. It is **supervisory
  guidance, not a binding rule**, and it does not apply to a proprietary trading
  firm, fund, or individual that is not a supervised banking organization. Cite it,
  if at all, as an analogy for independent validation and documented model
  governance — not as an obligation.
  https://www.federalreserve.gov/supervisionreg/srletters/sr2602.htm ·
  https://www.occ.gov/news-issuances/bulletins/2026/bulletin-2026-13.html

Where a robustness result feeds an actual risk limit, the enforcement of that limit
is a separate control that must be independent of strategy logic
(`kill-switch-and-drawdown-circuit-breakers`).
