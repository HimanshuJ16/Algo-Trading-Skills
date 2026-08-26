# Standards — model-monitoring-dashboard-for-non-technical-stakeholders

**No regulator, exchange or standards body publishes a model-health traffic-light
scheme for trading models, or thresholds for one.** Every band on this page is
either a cited industry rule of thumb or this library's default. None of them is a
compliance floor. Calibrate before use and record the rationale alongside the model
card (`model-card-documentation-for-trading-models`).

## Traffic-light bands as implemented

| Health metric | GREEN | AMBER | RED | Edge convention |
|---|---|---|---|---|
| Prediction Accuracy | $\ge 55\%$ | $50\% \le a < 55\%$ | $< 50\%$ | Edges are GREEN-inclusive |
| Model Age | $\le 14$ days | $15$–$30$ days | $> 30$ days | Edges are GREEN-inclusive |
| Feature Drift PSI | $< 0.10$ | $0.10 \le \text{PSI} < 0.25$ | $\ge 0.25$ | Edges belong to the **worse** band |
| Inference Latency | $\le$ `latency_green_max_ms` | up to `latency_amber_max_ms` | above it | Edges are GREEN-inclusive |

The PSI row uses the opposite edge convention from the other three deliberately: it
reproduces the cited rule of thumb exactly as that source states it (below). The
other three have no external source to reproduce, so they use inclusive-GREEN, which
is the least surprising reading of "GREEN if age $\le 14$ days".

Any metric that was not reported, and any latency with no configured budget, grades
**AMBER** with `measured=False` and `value=None`. A dashboard that renders GREEN for
an unmeasured metric converts a monitoring outage into positive assurance.

## What the three colours mean

The semantics are taken from the Basel Framework's backtesting zones — the closest
thing in financial regulation to a supervisory traffic light over model quality.
Chapter **MAR32.7–32.9**, *Internal models approach: backtesting and P&L attribution
test requirements*:

> Green zone. This corresponds to results that do not themselves suggest a problem
> with the quality or accuracy of a bank's model.
> Amber zone. This encompasses results that do raise questions in this regard, for
> which such a conclusion is not definitive.
> Red zone. This indicates a result that almost certainly indicates a problem with a
> bank's risk model.

Two things are worth carrying across and one is worth not carrying across:

- **Amber means unresolved, not slightly bad.** That is precisely why an unmeasured
  metric belongs in amber rather than green.
- **Basel's boundaries are derived, these are not.** MAR32.9's Table 1 (green 0–4,
  amber 5–9, red 10 or more exceptions over 250 observations) states the zones are
  "defined according to the number of exceptions generated in the backtesting
  programme considering statistical errors as explained in MAR99.9 to MAR99.21" —
  i.e. from binomial type I and type II error rates. The accuracy, model-age and
  latency bands in this skill have no such derivation.
- **Basel MAR32 does not apply here.** It governs the market-risk capital models of
  banks under the Basel Framework, not the ML signal models of trading firms. It is
  cited as a design precedent for the vocabulary, not as an applicable requirement.

## Population Stability Index bands

$$\text{PSI} = \sum_{i=1}^{B} (q_i - p_i)\,\ln\!\left(\frac{q_i}{p_i}\right)$$

The $0.10$ / $0.25$ bands are the Lewis (1994) rule of thumb, stated by Yurdakul &
Naranjo (2020) as: PSI $< 0.10$ "little change", $0.10 \le$ PSI $< 0.25$ "moderate
change", $0.25 \le$ PSI "significant change, action required". This engine's edges
match that statement exactly, which is why $0.10$ is AMBER and $0.25$ is RED.

**It is a rule of thumb, not a test.** Yurdakul & Naranjo report that these
benchmarks are used "without reference to statistical type I or type II error
rates", that at $B = 10$ the $0.25$ benchmark "seems reasonable for sample sizes $n$
and $m$ between 100 and 200, but it is too conservative for larger sample sizes",
and that the fixed $0.10$ and $0.25$ tests have **powers that decrease with sample
size**. Monitoring windows in trading are routinely far larger than 200
observations — exactly the regime where the fixed band goes blind. Their chi-square
benchmark, $\text{PSI} > \left(\tfrac{1}{n} + \tfrac{1}{m}\right)\chi^{2}_{\alpha,\,B-1}$,
is the calibrated alternative; `concept-drift-vs-staleness-differentiation`
implements it.

This dashboard grades a PSI value it is given. It cannot detect a PSI that was
computed with bounded bin edges, averaged across features, or collapsed to $0.0$ by
de-duplicated quantile edges on a sparse indicator. Those failure modes and their
measured magnitudes are documented in
`concept-drift-vs-staleness-differentiation/references/standards.md`.

## The accuracy floor is not a break-even point

The pre-2.0 implementation described accuracy below 50% as "below break-even
threshold". That is only true for a symmetric, cost-free payoff. For a strategy with
average gross win $W$, average gross loss $L$ and round-turn cost $c$ per trade,
expected PnL per signal is $p(W - c) - (1-p)(L + c)$, so the break-even hit rate is

$$p^{*} = \frac{L + c}{W + L}$$

| $W$ | $L$ | $c$ | $p^{*}$ |
|---|---|---|---|
| 1 | 1 | 0 | 50.0% |
| 1 | 1 | 0.05 | 52.5% |
| 2 | 1 | 0 | 33.3% |
| 1 | 2 | 0 | 66.7% |

A 60%-accurate model with $W = 1, L = 2$ loses money; a 40%-accurate model with
$W = 2, L = 1$ makes it. Directional accuracy is a *health* metric, not a
profitability metric, and the floor must be calibrated from the strategy's own
payoff profile. Track profitability separately — see
`strategy-performance-decay-detection-vs-market-wide-decay` and
`risk-adjusted-performance-attribution-per-strategy`.

## Engineering requirements

| Requirement | Rationale |
|---|---|
| An unmeasured metric MUST NOT grade GREEN, and its value MUST be null, not `0.0`. | A monitoring outage otherwise renders as positive assurance, and a `0.0` PSI reads as perfect stability for a statistic that was never computed. |
| A negative model age MUST be rejected, not graded. | A clock skew or a broken `last_retrained_at` yields a negative age that passes any `age > limit` test unchallenged and renders as the freshest model on the board. |
| A negative PSI and an accuracy outside $[0, 100]$ MUST be rejected. | PSI is non-negative by construction; an accuracy of 150% is a unit error. Both graded GREEN prior to v2.0.0. |
| Non-finite telemetry MUST raise, never grade. | Every `>=` comparison against `NaN` is `False`, so a threshold ladder walks past each band and lands on whichever branch is last — a verdict reached by arithmetic on a missing number. |
| Aggregation MUST be the worst component, never an average. | Averaging is the failure this skill exists to prevent: a 30%-accuracy collapse averaged against three healthy components produces a comfortable score. |
| The headline MUST name the components that drove the colour. | An unattributed colour sends a non-technical reader back to the raw telemetry the dashboard was built to replace. |
| A missing metric MUST NOT recommend a retrain. | It sends the operator to rebuild the model when the fault is in the exporter, and a retrain fitted on a degraded feed is worse than no retrain. |
| The recommended action MUST be presented as advisory. | The module cancels nothing and halts nothing. If the runbook does not name who executes the halt, RED is a colour and not a control. |

## Regulatory touchpoints

None of the following prescribes a model-health metric, threshold or colour. They
govern who must be able to read the monitor and what must happen after it fires.

**EU — ESMA Supervisory Briefing on Algorithmic Trading in the EU**,
ESMA74-1505669079-10311, 26 February 2026. Issued under Article 29(2) of the ESMA
Regulation; its content is **non-binding and not subject to a "comply or explain"
mechanism** (¶3).

| Point | Location |
|---|---|
| RTS 6 Article 16(1) requires real-time monitoring of all algorithmic trading activity to detect signs of disorderly trading — activity, not model quality | ¶94 |
| RTS 6 Article 16(2) requires **two lines of defence**: monitoring by the trader in charge of the algorithm, and by the risk management function or an independent risk control function | ¶95 |
| That function must not be hierarchically dependent on the trader, must be endowed with appropriate powers, tools and procedures to challenge the trader, and must not be directly involved in the trader's activity; an IT function is explicitly not so endowed | ¶96 |
| Testing is required following each material change or substantial update; a material change is "any modification that may alter the behaviour, risk profile, or compliance posture" of an algorithm, and firms must timestamp, approve and record all material changes | ¶30–31 |
| "Retraining or modifying machine learning components" appears under *Adaptive Capabilities* in the change-type table where good practice is to consider retesting | ¶31 |
| Firms should manage the risk that a series of minor recalibrations accumulates, unchecked, into a material change in model output without being tested | ¶30, ¶47 |
| NCAs should assess how firms take the use of AI into account in the RTS 6 Article 9 annual self-assessment and validation | ¶49 |
| Under RTS 6 Article 2 compliance staff must have at least a general understanding of how the firm's algorithmic trading systems operate and be in continuous contact with technical staff; systems should be explainable, and it is the firm's responsibility to explain how AI impacts decision-making | ¶50 |

**EU — Commission Delegated Regulation (EU) 2017/589 (MiFID II RTS 6), Article 9,
"Annual self-assessment and validation"**. Binding on investment firms engaged in
algorithmic trading in the EU. Requires an annual self-assessment and validation
process and a validation report covering the firm's algorithmic trading systems,
trading algorithms and strategies, its governance/accountability and approval
framework, its business continuity arrangements, and overall compliance with Article
17 of MiFID II. Retained dashboard snapshots are evidence for that report; they do
not discharge the obligation, and *annual* is a floor, not a substitute for
continuous monitoring.

**Not found — do not claim it.** No provision of MiFID II, RTS 6, SEC Rule 15c3-5,
FINRA rules, or the Basel Framework specifies a model-health traffic-light scheme, a
PSI threshold, an accuracy floor, a maximum model age, or a monitoring frequency for
ML trading models. Any document asserting a "regulatory 0.25 PSI limit" or a
"required 55% accuracy" is wrong.

## Verification status of the claims on this page

| Claim | Source consulted | Result |
|---|---|---|
| PSI rule-of-thumb bands and their lack of error-rate control | Yurdakul & Naranjo (2020), as reproduced in `concept-drift-vs-staleness-differentiation/references/standards.md` and confirmed by search against the paper's abstract | Verified |
| Basel green/amber/red zone definitions, Table 1 boundaries, and their binomial derivation | BIS-generated PDF export of Basel Framework MAR32 (version effective 01 Jan 2023), §§32.7–32.9, text extracted and read directly. The live BIS chapter page renders its content via JavaScript and could not be read directly during this audit | Verified verbatim against the PDF export |
| ESMA briefing paragraphs ¶3, ¶30–31, ¶47, ¶49, ¶50, ¶94–96 | ESMA74-1505669079-10311 PDF, text extracted and read directly | Verified verbatim |
| RTS 6 Article 9 scope | ESMA briefing ¶49, which restates the article; the EUR-Lex text itself did not render for direct retrieval during this audit | Verified via ESMA's restatement, not against the primary text |
| Break-even hit rate $p^{*} = (L+c)/(W+L)$ | Elementary derivation, shown above | Derived, no source required |

## References

- Basel Committee on Banking Supervision, *Basel Framework, MAR32: Internal models
  approach: backtesting and P&L attribution test requirements*, version effective
  01 Jan 2023, §§32.7–32.9. <https://www.bis.org/basel_framework/chapter/MAR/32.htm>
- ESMA (2026). *Supervisory Briefing on Algorithmic Trading in the EU*,
  ESMA74-1505669079-10311, 26 February 2026.
  <https://www.esma.europa.eu/sites/default/files/2026-02/ESMA74-1505669079-10311_Supervisory_Briefing_on_Algorithmic_Trading_in_the_EU.pdf>
- Commission Delegated Regulation (EU) 2017/589 (MiFID II RTS 6), Articles 2, 9
  and 16. <https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng>
- Yurdakul, B. & Naranjo, J. (2020). "Statistical properties of the population
  stability index." *Journal of Risk Model Validation* 14(4), 89–100.
  DOI: 10.21314/JRMV.2020.227.
  <https://www.risk.net/journal-of-risk-model-validation/7725371/statistical-properties-of-the-population-stability-index>

## Category

`financial-ml`
