# Standards & Coverage — model-staleness-detection

## Statistic definitions

### Population Stability Index

$$\mathrm{PSI} = \sum_i (a_i - e_i)\,\ln\!\left(\frac{a_i}{e_i}\right)$$

over bins, where $e_i$ is the reference (training) proportion in bin $i$ and
$a_i$ the current proportion. It is symmetric in $a$ and $e$, and is the
J-divergence of Jeffreys (1946) — the sum of the two directional
Kullback–Leibler divergences.

Two consequences the implementation depends on:

- **The Gaussian closed form is $z^2$, not $z^2/2$.** For two normals with
  equal variance $\sigma^2$ whose means differ by $\delta$, each directional KL
  is $\delta^2/2\sigma^2$, so the J-divergence is $\delta^2/\sigma^2 = z^2$.
  One-directional KL is *half* of PSI; a monitor that reports $0.5z^2$ and
  compares it to the 0.10/0.25 bands is off by a factor of two in the
  conservative direction.
- **A location statistic is not PSI.** $z$ depends only on the means, so it is
  zero for any pure variance or shape change. `scripts/staleness_monitor.py`
  therefore prefers binned PSI against a stored reference *sample*, and falls
  back to the Gaussian form only when the baseline is mean/std alone — the
  fallback is labelled `GAUSSIAN_JEFFREYS` in `FeatureDriftResult.method` so
  the weaker measurement is visible rather than implied.

**Threshold provenance.** The familiar bands — PSI < 0.10 "no material shift",
0.10–0.25 "moderate, monitor", ≥ 0.25 "major shift" — come from credit-scoring
practice, popularised by Siddiqi, *Credit Risk Scorecards* (Wiley), which
describes them as industry rules of thumb and explicitly not definitive. They
have **no controlled error rate**, and their power depends on sample size, so
they are operator-chosen defaults in this skill, not a test. Where a calibrated
alternative is needed, see the chi-square benchmark discussed in
`concept-drift-vs-staleness-differentiation/references/standards.md`.

**Zero-bin floor.** An empty bin makes $\ln(a/e)$ undefined, so proportions are
floored at $10^{-4}$ by convention. The *magnitude* of PSI for two distributions
with disjoint support is therefore an artefact of that constant. Read a very
large PSI as "disjoint", never as "$n$ times worse".

### Wilson score interval

Wilson, E. B. (1927), "Probable inference, the law of succession, and
statistical inference", *JASA* 22(158):209–212. The one-sided lower bound at
confidence $1-\alpha$ is

$$\frac{\hat{p} + \frac{z^2}{2n}}{1 + \frac{z^2}{n}} - \frac{z}{1 + \frac{z^2}{n}}\sqrt{\frac{\hat{p}(1-\hat{p})}{n} + \frac{z^2}{4n^2}}$$

Preferred over the normal approximation because it stays inside $[0,1]$ and
keeps sensible coverage at the small windows and extreme proportions a trading
monitor actually runs on — at $\hat p = 1$ the normal approximation gives an
interval of zero width, which is exactly the case where a monitor is most
likely to be over-confident.

### Sampling noise in a rolling hit rate

Directional accuracy over a window of $n$ realised outcomes is a binomial
proportion with standard error $\sqrt{p(1-p)/n}$. At $n = 60$ and a true
$p = 0.55$ that is 6.4pp, and the exact binomial probability of the window
falling below 0.52 is **0.347**. This is the arithmetic behind requiring a
breach to persist across consecutive evaluations rather than halting on one
window; three consecutive breaches on independent windows would occur about 4%
of the time under the same assumptions. Overlapping windows are positively
correlated, so treat that as a lower bound on the false-halt rate, and evaluate
on a cadence matched to the label horizon rather than per tick.

## Framework coverage

| Framework / Engine | Relevance to this skill |
|---|---|
| Python standard library (`statistics`, `bisect`, `collections`) | The reference implementation has no third-party dependency; `NormalDist.inv_cdf` supplies the normal quantile for the Wilson bound. |
| Model-monitoring platforms (Evidently, MLflow, Great Expectations, WhyLabs) | Compute equivalent drift statistics. Verify each platform's PSI binning convention — outer-edge handling and quantile de-duplication differ, and both change the number materially. This skill does not integrate with them. |
| Production inference engines | Consume `ModelStalenessReport.sizing_multiplier` and the halt status; the halt should be wired to the venue-level control in `kill-switch-and-drawdown-circuit-breakers`. |

## Category

`financial-ml` — see the top-level `mappings/` directory for how this category
rolls up across the full skill library.

## Regulatory & governance touchpoints

None of the following mandates this skill's specific thresholds or statistics.
They are the governance context a firm's model-monitoring programme sits in;
apply only the jurisdictions the firm is actually subject to.

| Jurisdiction | Instrument | Status as of 2026-08 | What it actually says |
|---|---|---|---|
| US (banking) | *Model Risk Management: Revised Guidance* — Federal Reserve **SR 26-2**, OCC Bulletin **2026-13**, issued 17 April 2026 by the Fed, OCC and FDIC | In force. **Supersedes SR 11-7 (2011) and SR 21-8 (2021)**, which are rescinded. | Interagency supervisory guidance on model development, validation, monitoring and governance, taking a risk-based approach. Expected to be most relevant to banking organisations with **over $30bn in total assets**; the agencies state it does not set forth enforceable standards, and that it does **not** apply to generative or agentic AI. Cite the 2026 guidance, not SR 11-7. |
| EU | Commission Delegated Regulation (EU) **2017/589** (MiFID II RTS 6), Article 9 | In force since 3 January 2018. | Requires an investment firm engaged in algorithmic trading to perform an **annual self-assessment and validation** of its algorithmic trading systems, procedures, controls and governance, reviewed by senior management. Applies to the firm's algorithmic trading systems generally, not to ML monitoring specifically. |
| India | SEBI circulars **SEBI/HO/MIRSD/DOS2/CIR/P/2019/10** (4 January 2019, market intermediaries) and the parallel circulars for MIIs and mutual funds | In force. | **Reporting** obligations on AI/ML applications offered or used. These are disclosure requirements, not model-monitoring mandates. |
| India | *Guidelines for Responsible Usage of AI/ML in Indian Securities Markets* | **Consultation paper, 20 June 2025 — not yet in force** as of August 2026; SEBI has said final guidelines are forthcoming. | The consultation proposes testing and monitoring of models, segregation of test and production environments, shadow testing, post-deployment monitoring for deviation, human oversight and kill-switch controls. Track it, but do not represent it as a current obligation. |

**Do not universalise these.** A proprietary trading firm outside the EU is not
subject to RTS 6; a non-bank quant fund is not subject to the US interagency
model-risk guidance. The defensible general statement is narrower: where a firm
is subject to any of them, an *undocumented, unmonitored* live model is the
harder position to defend, and the artefacts this skill produces — a
pre-committed threshold, an attributable halt-clearing record, a reproducible
rolling metric — are the ones an examiner asks for.

## Sources

- Federal Reserve, [SR 26-2: Revised Guidance on Model Risk Management](https://www.federalreserve.gov/supervisionreg/srletters/SR2602.htm), 17 April 2026.
- OCC, [Bulletin 2026-13: Model Risk Management: Revised Guidance](https://www.occ.gov/news-issuances/bulletins/2026/bulletin-2026-13.html), 17 April 2026.
- [Commission Delegated Regulation (EU) 2017/589 (RTS 6)](https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng).
- SEBI, [Reporting for AI and ML applications and systems offered and used by market intermediaries](https://www.sebi.gov.in/legal/circulars/jan-2019/reporting-for-artificial-intelligence-ai-and-machine-learning-ml-applications-and-systems-offered-and-used-by-market-intermediaries_41546.html), 4 January 2019.
- SEBI, [Consultation Paper on guidelines for responsible usage of AI/ML in Indian securities markets](https://www.sebi.gov.in/reports-and-statistics/reports/jun-2025/consultation-paper-on-guidelines-for-responsible-usage-of-ai-ml-in-indian-securities-markets_94687.html), June 2025.
