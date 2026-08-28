# Standards — strategy-lifecycle-retirement-criteria

## Read this first

**No regulator, exchange, or standards body prescribes a minimum Information Ratio, a
maximum drawdown multiple, an IC t-statistic floor, or a return-drift limit for
withdrawing a trading strategy from production.** Retiring a strategy for economic
underperformance is a business and governance decision. Every threshold in this skill is
a house parameter. Do not cite a regulator in support of any of them.

What *is* regulated is adjacent and materially different: the operational and
market-integrity controls around algorithmic trading, and — in the EU — an annual
obligation to review and validate the strategies you run. Those obligations are what this
engine's output feeds; they are not what it enforces.

## Engine defaults (calibrate before use)

| Parameter | Default | What it actually does |
|---|---|---|
| `min_live_information_ratio` | $0.50$ | Floor on live IR. Breach: `ALPHA_DECAY_IR`. Strict `<` — exactly $0.50$ passes. |
| `max_drawdown_multiplier` | $1.50$ | Live DD may reach $1.5\times$ the backtested max DD. Breach: `DRAWDOWN_BREACH`. Strict `>` — exactly at the limit passes. |
| `min_ic_t_stat` | $1.96$ | Floor on the supplied IC t-statistic. Breach: `IC_STATISTICAL_DECAY`. |
| `max_allowed_performance_drift_pct` | $-40.0$ | Live may retain no less than 60% of the backtested annual return. Breach: `PERFORMANCE_DRIFT`. |
| `min_backtest_return_for_drift_pct` | $1.0$ | Backtested annual returns below this magnitude make the ratio-based drift meaningless; the criterion is skipped and disclosed rather than evaluated. Heuristic. |
| `mandatory_retirement_breach_count` | $3$ | Breaches at which retirement becomes mandatory. Capped at 4 — only four criteria exist. **Setting it to 4 makes ladder-based retirement unreachable whenever the drift criterion is skipped** (three evaluable criteria cannot produce four breaches); only the escalation override can then retire a strategy. |
| `escalation_ir_floor` | $0.0$ | A drawdown breach with live IR below this escalates to `MANDATORY_RETIREMENT` at two breaches. |
| `min_live_observations` | `None` | Sample-size gate. **`None` means no gate.** Set it, and supply `live_observation_count`, or the ladder will retire a three-week-old strategy on noise. |

Archive `StrategyRetirementReport.thresholds_applied` with every decision. A
threshold-dependent adjudication is not reproducible without the thresholds.

## Where the 0.50 IR default comes from

| Fact | Source | Applied here |
|---|---|---|
| Grinold & Kahn's active-manager percentile table places IR $=1.0$ at the 90th percentile, $0.50$ at the 75th, $0.0$ at the 50th, $-0.50$ at the 25th | *Active Portfolio Management*, 2nd ed. (McGraw-Hill, 2000), p. 114, as reproduced by [Wealthfront Engineering, "Quantifying Investing Skill: The Information Ratio"](https://eng.wealthfront.com/2011/01/26/quantifying-investing-skill-information/) (secondary source — the book itself was not consulted directly) | The $0.50$ default is the **top-quartile** manager level. Setting it as a *retirement floor* means retiring anything below top quartile — aggressive by construction, and a deliberate choice you should make consciously. The table describes long-only active managers circa 2000; a market-neutral quant book is not that population. |
| Information ratio = mean return in excess of a benchmark ÷ the standard deviation of that excess return (tracking error) | [AnalystPrep, CFA Level II — Fundamental Law of Active Portfolio Management](https://analystprep.com/study-notes/cfa-level-2/state-and-interpret-the-fundamental-law-of-active-portfolio-management-including-its-component-terms-transfer-coefficient-information-coefficient-breadth-and-active-risk-aggressiveness/) | The engine consumes a pre-computed IR. For an absolute-return strategy with no benchmark, IR collapses to a Sharpe ratio at a zero risk-free rate — say which you supplied, because the $0.50$ threshold was calibrated against the benchmark-relative quantity. |
| Fundamental law of active management: $\text{IR} \approx \text{IC} \times \sqrt{\text{BR}}$, where BR is the number of *independent* decisions per year | Grinold (1989), as summarised in the CFA curriculum note above and [Robeco, "Fundamental Law of Active Management"](https://www.robeco.com/en-int/insights/2018/04/fundamental-law-of-active-management-shows-way-to-higher-information-ratio) | Explains why criteria 1 and 3 are **not independent**: IR and IC are two views of the same skill. Do not read "IR and IC t-stat both breached" as two confirmations. |

## Where the 1.96 IC t-stat default comes from — and what it does not mean

| Claim | Status | Applied here |
|---|---|---|
| $1.96$ is the **two-tailed** 5% critical value of the standard normal distribution; the one-sided 5% value is $1.645$ | Standard result (the 0.975 and 0.95 quantiles of $N(0,1)$) | "Has the IC decayed to non-positive?" is a **one-sided** question. Describing $1.96$ as "95% confidence" for that question is imprecise — it is a one-sided 97.5% test. The skill states the threshold as a number, not as a confidence level. |
| $1.96$ is a large-sample normal approximation; finite-sample $t$ critical values are larger and depend on degrees of freedom | Standard result | The engine does not know the sample size behind the supplied t-stat, and cannot correct for it. This is why `min_live_observations` exists. |
| A t-statistic computed on **overlapping** forecast windows is inflated by serial correlation unless a heteroskedasticity- and autocorrelation-consistent estimator is used | Newey & West (1987), "A Simple, Positive Semi-Definite, Heteroskedasticity and Autocorrelation Consistent Covariance Matrix", *Econometrica* 55(3) | The engine consumes the t-stat as an opaque number. If it was computed on overlapping windows without a HAC adjustment, the same $1.96$ is a materially weaker test than it appears. Record how the t-stat was computed. |
| For a *newly discovered* factor drawn from a heavily data-mined universe, the appropriate hurdle is a t-ratio above $3.0$, not $2.0$: "given this extensive data mining, it does not make any economic or statistical sense to use the usual significance criteria for a newly discovered factor, e.g., a t-ratio greater than 2.0" | Harvey, Liu & Zhu (2016), "…and the Cross-Section of Expected Returns", *Review of Financial Studies* 29(1) — [Oxford Academic](https://academic.oup.com/rfs/article/29/1/5/1843824), [NBER w20592](https://www.nber.org/papers/w20592) | **Applies to selecting a strategy, not to retiring one.** The multiple-testing haircut is a discovery-stage correction; the live-monitoring question here is a single out-of-sample hypothesis. Cite it when setting the *promotion* bar (`paper-to-live-promotion-checklist`), not to argue this engine's retirement floor should be $3.0$. |

## Regulatory position (verified — read before citing anything)

### European Union

| Claim | Source | Status |
|---|---|---|
| "According to Article 9 of RTS 6, an investment firm shall annually perform a self-assessment and validation process and on the basis of that process issue a validation report. In the course of that process the investment firm shall review, evaluate and validate the following: (a) its algorithmic trading systems, trading algorithms and algorithmic trading strategies…" | ESMA, *Supervisory Briefing on Algorithmic Trading in the EU*, ESMA74-1505669079-10311 (26 Feb 2026), ¶49 — [ESMA](https://www.esma.europa.eu/sites/default/files/2026-02/ESMA74-1505669079-10311_Supervisory_Briefing_on_Algorithmic_Trading_in_the_EU.pdf) | **Mandatory** for EU/EEA investment firms engaged in algorithmic trading. This is the obligation a documented retirement rule supports: a defensible answer to "how did you review and validate this strategy?" It prescribes **no numeric performance threshold**. |
| Self-assessments must be conducted "on an article-by-article basis, covering all relevant articles of RTS 6" and "indicate per relevant article whether a firm considers itself compliant" | ibid. ¶51 | Supervisory guidance. Format expectation for the validation report the engine's output feeds. |
| Article 9(2)–(5) of RTS 6 "places primary responsibility for the self-assessment within a firm's risk management function"; where applicable it "must be subject to review by a firm's internal audit function and must receive approval from senior management" | ibid. ¶53 | **Mandatory.** Governance ownership sits with risk management and senior management — **not** with the researcher who built the strategy. This is the structural reason the retirement rule must be external to the strategy team. |
| "A material change or substantial update is any modification that may alter the behaviour, risk profile, or compliance posture of an algorithm… Investment firms are required to timestamp, approve, and record all material changes." The illustrative table lists **"Risk Controls — Changing thresholds, kill switch logic, or alert triggers"** | ibid. ¶31 | **Mandatory** for the recording obligation; the change-type table is good practice. Directly relevant: **re-tuning this engine's thresholds is itself a change requiring approval and a timestamped record.** |
| Firms "should manage the risk that a series of minor or small changes due to recalibrations could accumulate over time, when uncontrolled or unchecked, into a material change" | ibid. ¶30 | Guidance. Argues against quietly loosening a retirement threshold one notch at a time. |

### United States

| Claim | Source | Status |
|---|---|---|
| "firms must have appropriate policies and procedures in place to review and test any trading algorithms they use, including development, deployment and **post-implementation monitoring** of algorithmic strategies" | FINRA, *Regulatory Notice 15-09* (March 2015) — [FINRA](https://www.finra.org/rules-guidance/notices/15-09) | The notice states **effective practices**, not rules; the underlying supervisory obligation is FINRA Rule 3110. Post-implementation monitoring is the category a retirement rule sits in. |
| Firms "should undertake a holistic review of their trading activity and consider implementing a cross-disciplinary committee to assess and react to the evolving risks associated with algorithmic strategies", most effective "when they include representation from areas outside of trading" | ibid., §I | Effective practice. Supports routing `REDUCE_ALLOCATION` and `MANDATORY_RETIREMENT` to a committee rather than to the strategy owner. A footnote adds that such committees "should not take the place of oversight… by appropriately registered personnel". |
| Firms should provide "mechanisms by which the firm may quickly disable the algorithm or supporting platform with a minimal number of steps" | ibid., §II | Effective practice. This is the *execution* side of a retirement decision — see `strategy-decommissioning-and-position-unwind-procedure`. |

**Scope warning.** Regulatory Notice 15-09 is aimed at market-integrity and operational
risk — wash sales, excessive message traffic, erroneous orders. A full-text scan of the
notice returns **zero** occurrences of "Sharpe", "information ratio", "drawdown", or
"t-statistic". The same is true of the ESMA supervisory briefing. Neither document
supports a numeric performance threshold for retirement, and citing either in support of
one misrepresents it.

## Known limitations

- **The four criteria are not independent.** IR and IC measure the same skill (fundamental
  law above); return drift moves with both. A breach count is a severity heuristic, not a
  statistical test, and the criteria are equally weighted by fiat.
- **No causal attribution.** A market-wide regime shift and a strategy-specific alpha
  decay are indistinguishable in this payload. Pair with
  `strategy-performance-decay-detection-vs-market-wide-decay` before acting.
- **No sample-size awareness unless you supply it.** `min_live_observations` defaults to
  `None`, which is no gate at all.
- **The drift criterion is a ratio.** It is undefined for a non-positive backtested return
  and unstable near zero; in those cases it is skipped and disclosed, and
  `return_gap_pct_points` is the fallback. It is never reported as `0.0`.
- **Drawdown magnitudes only.** The negative convention is rejected rather than converted,
  because silently guessing the convention inverts the drawdown criterion.
- **Nothing here measures capacity, crowding, or transaction-cost drift**, all of which
  cause live underperformance that these four criteria will misattribute to alpha decay.

## Category

`Investment Governance & Capital Allocation` — see top-level `mappings/` directory.
