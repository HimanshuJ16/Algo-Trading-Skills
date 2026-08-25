# Standards — feature-importance-drift-monitoring

## Configuration defaults (calibrate before use)

**No regulator, exchange or standards body publishes a feature-importance drift
threshold.** The values below are this library's defaults. The 0.70 rank-agreement
floor in particular is a convention, not a finding — calibrate it against the model's
own window-to-window distribution of $\rho$ during a period when the model was known
to be healthy, and record the rationale alongside the model card.

| Parameter | Default | What it actually does |
|---|---|---|
| `min_spearman_rank_threshold` | $0.70$ | Alert floor on the tie-corrected rank agreement between the baseline and live importance rankings, over the common feature set. |
| `max_degradation_drop_pct` | $0.80$ | A top-N baseline feature that loses more than this fraction of its importance **share** is flagged, even when overall rank agreement is high. Exclusive: a drop of exactly 80% does not trigger. |
| `top_n_monitored_features` | $3$ | How many of the highest-importance baseline features are subject to the degradation check. Ranked over the *whole* baseline profile, so a feature the live profile dropped is still recognised as top-N. |
| `min_common_features` | $3$ | Hard floor, enforced in the constructor. Below 3 the coefficient is degenerate — see the table below. |
| `min_feature_set_overlap_ratio` | $1.0$ | Fraction $|common| / |union|$ of the two feature sets required before the comparison is treated as like-for-like. At the default, any dropped or newly-appearing feature raises an alert. |

## Coefficient facts (derived, reproducible)

Spearman's $\rho$ is defined as the Pearson correlation of the **rank variables**. The
shortcut $\rho = 1 - 6\sum d_i^2 / (M(M^2-1))$ is valid **only when all $M$ ranks are
distinct integers**; with ties it yields incorrect results, and tied features must
instead receive mid-ranks (the average of the positions they span)
([Spearman's rank correlation coefficient, Wikipedia](https://en.wikipedia.org/wiki/Spearman%27s_rank_correlation_coefficient)).
Feature-importance vectors tie constantly, because every feature the model does not
use has an importance of exactly 0.0.

The table below is obtained by exhaustively enumerating all $M!$ reorderings of $M$
ranks — reproducible in a few lines of Python, no external source required:

| $M$ | Attainable values of $\rho$ | $P(\rho \ge 0.70)$ under a uniformly random reordering |
|---|---|---|
| 3 | $\{-1, -0.5, +0.5, +1\}$ only | $1/6 = 16.7\%$ |
| 4 | the multiples of $0.2$ | $4/24 = 16.7\%$ |
| 5 | the multiples of $0.1$ | $14/120 = 11.7\%$ |
| 6 | — | $49/720 = 6.8\%$ |
| 7 | — | $222/5040 = 4.4\%$ |
| 8 | — | $1161/40320 = 2.9\%$ |

Two consequences worth internalising before trusting a threshold:

- At $M = 3$ a 0.70 floor means *"identical ordering or alert"* — there is no
  attainable value between $0.5$ and $1.0$.
- At $M = 4$ the threshold sits between the attainable $0.6$ and $0.8$, so it fires on
  anything worse than a single adjacent swap, and a completely random reordering still
  passes one time in six.

## Importance-metric facts (verified against the primary source)

Source: scikit-learn user guide,
[Permutation feature importance](https://scikit-learn.org/stable/modules/permutation_importance.html).

| Fact | Consequence for this skill |
|---|---|
| "impurity-based feature importance for trees is strongly biased and favor high cardinality features" | Gain/impurity importance and permutation/SHAP importance are not interchangeable. Never baseline on one and monitor with the other. |
| "When two features are correlated and one of the features is permuted, the model still has access to the latter through its correlated feature. This results in a lower reported importance value for both features" | Rank churn between correlated features (`rsi_14` vs `rsi_21`) is estimator noise. Cluster and monitor one representative, or expect a permanently depressed $\rho$. |
| "Features that are important on the training set but not on the held-out set might cause the model to overfit" | Baseline and live importance must both be computed on comparable held-out data, or the drift measured is the train/test gap, not regime change. |

## Regulatory touchpoints

None of the following prescribes a drift metric or threshold. They govern what a firm
must do *around* the monitor — in particular, what happens after it fires.

**EU — ESMA Supervisory Briefing on Algorithmic Trading in the EU**,
ESMA74-1505669079-10311, 26 February 2026
([PDF](https://www.esma.europa.eu/sites/default/files/2026-02/ESMA74-1505669079-10311_Supervisory_Briefing_on_Algorithmic_Trading_in_the_EU.pdf)).
Explicitly **non-binding** and not subject to a comply-or-explain mechanism (¶3); it
guides NCAs and investment firms on supervising algorithmic trading under MiFID II.

| Point | Location |
|---|---|
| Testing is required following each material change or substantial update; a material change is "any modification that may alter the behaviour, risk profile, or compliance posture" of an algorithm or strategy, and firms are required to timestamp, approve and record all material changes | ¶30–31 |
| "Retraining or modifying machine learning components" is listed under *Adaptive Capabilities* in the change-type table where good practice is to consider retesting | ¶31 |
| Firms should manage the risk that a series of minor recalibrations accumulates, unchecked, into a material change in the model output without being tested | ¶30, ¶47 |
| NCAs should assess how firms take the use of AI into account in the RTS 6 Article 9 annual self-assessment and validation | ¶49 |
| Algorithmic trading systems and algorithms should be explainable, and it is the firm's responsibility to be able to explain how AI impacts the algorithm's decision-making (via RTS 6 Article 2 compliance-staff understanding) | ¶50 |
| AI-based algorithmic trading is **currently excluded** from the AI Act (Regulation (EU) 2024/1689) high-risk use-case scope, though that scope is subject to annual review | ¶43, ¶46 |

**EU — Commission Delegated Regulation (EU) 2017/589 (MiFID II RTS 6), Article 9,
"Annual self-assessment and validation"** —
https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng. Binding on investment firms
engaged in algorithmic trading in the EU. Requires an annual self-assessment and
validation process, issuing a validation report, covering the firm's algorithmic
trading systems, trading algorithms and strategies, its governance/accountability and
approval framework, its business continuity arrangements, and overall compliance with
Article 17 of Directive 2014/65/EU. An importance-drift monitor produces evidence for
that report; it does not discharge the obligation, and *annual* is a floor, not a
substitute for continuous monitoring.

**US — Federal Reserve SR 26-2, "Revised Guidance on Model Risk Management",
17 April 2026** —
https://www.federalreserve.gov/supervisionreg/srletters/sr2602.htm. Replaces SR 11-7
(4 April 2011) and SR 21-8. **Applicability is narrow**: banking organisations with
more than $30 billion in total assets supervised by the Federal Reserve. Most
proprietary trading firms and fund managers are not in scope — cite it only if the
entity actually is. Do not cite SR 11-7 as current guidance.

**Not found — do not claim it.** No provision of MiFID II, RTS 6, SEC Rule 15c3-5,
FINRA rules or the AI Act specifies a feature-importance drift metric, a rank
correlation threshold, or a monitoring frequency for ML feature importance. Any
document asserting a "regulatory 0.70 Spearman requirement" is wrong.

## Change-control consequence

Because retraining an ML component is itself a change type ESMA flags for retesting
(¶31), an automated pipeline that retrains **and redeploys** on a drift alert converts
a monitoring control into an untested, unapproved change to a live trading algorithm.
The supported pattern is: alert → recorded, timestamped, approved change → testing →
controlled deployment. See `model-versioning-and-rollback` and
`canary-releases-for-strategy-code-changes`.

## Category

`financial-ml`
