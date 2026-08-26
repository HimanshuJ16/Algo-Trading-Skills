# Standards for Champion-Challenger Model A/B Testing

Two kinds of statement live on this page and they are not interchangeable:
**statistical results**, which are cited or measured, and **regulatory
material**, which applies only to specific entities in specific jurisdictions
and is flagged as such. No regulator mandates a $p$-value threshold, a minimum
sample size, or a traffic split. Every number in the engineering table below is
a defensible default to be calibrated, not a compliance floor.

## Engineering requirements

| Requirement | Rationale |
|---|---|
| The $p$-value MUST be drawn from the $t$ distribution with Welch-Satterthwaite $\nu$, never from the normal distribution. | The normal CDF understates $p$ at every finite $\nu$, so the error is always in the direction of over-promotion. Measured under the null (20,000 trials per cell, equal variances): false-promotion rate 2.79% at $N=30$ per arm and 4.43% at $N=5$, against a 2.50% nominal rate. |
| A zero sample variance MUST NOT be floored to a small constant. | Two constant return series carry no information about sampling variability. Substituting $10^{-6}$ produces $\lvert t \rvert \approx 35{,}000$ and $p = 0.0$ from a stubbed or replayed feed. Welch's statistic is genuinely $0/0$ when *both* variances vanish; one zero-variance arm remains well defined and is still testable. |
| $N_{\text{min}}$ and $\alpha$ MUST be fixed before data collection. | Optional stopping invalidates the fixed-horizon $p$-value. Measured under the null with the exact Welch test, evaluating after every new sample from $N=30$ to $N=200$: false-promotion rate 12.0% against a 2.50% nominal rate. |
| Statistics that were not computed MUST be `None`, never `0.0` or `1.0`. | A short-circuited report carrying `p_value = 1.0` puts "tested, found nothing" on a dashboard for a test that never ran. |
| Non-finite returns MUST produce their own status, never a verdict. | Every comparison against `NaN` is `False`, so a poisoned sample propagates to a `NaN` statistic that fails the significance test and reports as a clean inconclusive result. |
| Sample provenance MUST be verified against the configured model ids. | Swapping the two argument lists inverts every recommendation — the engine advises rejecting the *better* model — and no downstream consumer can detect it. |
| An unrecognised `test_mode` MUST raise, never default. | Falling through to `LIVE_SPLIT` on an unrecognised string routes real orders to an unvalidated model. `'shadow'` is not `'SHADOW'`. |
| The allocation hash MUST be salted with `experiment_id`. | An unsalted hash buckets every key identically in every experiment. Measured: 100% allocation agreement between two nominally independent 50/50 experiments, where ~50% is expected. |
| A finite sample whose variance overflows MUST return invalid data, not raise. | Squaring a deviation of ~1e200 raises `OverflowError` in Python; saturating to `inf` instead yields a `NaN` $t$ and a `NaN` $\nu$, which fail every comparison and report as "no significant difference". Returns on that scale are implausible as basis points and entirely plausible as a mis-parsed sentinel value. |
| The experiment config MUST be immutable after validation. | Validation runs once at construction. A mutable config can be walked past every check it just passed - assigning the plain string `'SHADOW'` afterwards leaves `test_mode` no longer identical to `TestMode.SHADOW` and routes live orders to the challenger. Statistically, a pre-registered experiment whose parameters can be edited mid-flight is not pre-registered. |
| The significance comparison MUST use the unrounded $p$. | Rounding to 4dp first makes $p = 0.049996$ report as `0.0500` and fail a `p < 0.05` gate it should pass. |
| The recommendation MUST remain advisory. | Promotion is a change to a live trading algorithm. See the regulatory section below. |

## Welch's two-sample $t$-test

Definitions follow the NIST/SEMATECH *e-Handbook of Statistical Methods*,
section 1.3.5.3 (Two-Sample $t$-Test for Equal Means), unequal-variance case.

$$t = \frac{\bar{X}_B - \bar{X}_A}{\sqrt{\dfrac{s_A^2}{n_A} + \dfrac{s_B^2}{n_B}}}
\qquad
\nu = \frac{\left(\dfrac{s_A^2}{n_A} + \dfrac{s_B^2}{n_B}\right)^{2}}
{\dfrac{(s_A^2/n_A)^2}{n_A - 1} + \dfrac{(s_B^2/n_B)^2}{n_B - 1}}$$

with $s^2$ the unbiased $(n-1)$ sample variance and the two-tailed
$p = 2\,\Pr(T_\nu > \lvert t \rvert)$. NIST is explicit that critical values and
$p$-values come from the $t$ distribution with $\nu$ degrees of freedom.
The implementation evaluates $\nu$ in the algebraically identical scale-free
form $\nu = 1 / \left(u^2/(n_A-1) + v^2/(n_B-1)\right)$, where
$u = (s_A^2/n_A) / (s_A^2/n_A + s_B^2/n_B)$ and $v = 1 - u$. This avoids
squaring the variance sum and so cannot overflow on large-magnitude input.

$\nu$ is generally non-integer and smaller than $n_A + n_B - 2$, which is what
makes the test conservative under unequal variances — and what the normal
approximation throws away.

Welch's test is preferred over Student's pooled-variance test here because a
challenger model has no reason to share the champion's return variance;
assuming it does is not a conservative simplification.

**Assumptions this module cannot check for you.** Observations must be
independent draws. Overlapping holding periods, one signal fanned out across
correlated symbols, and multiple trades from a single intraday move all violate
that and understate the true variance, inflating $t$. The test compares *means*,
so on a fat-tailed per-trade return distribution a 30-sample mean is not a
stable estimate regardless of what the $p$-value says.

**Effective significance level.** Promotion requires $p < \alpha$ *and* a
positive mean difference, so the one-sided false-promotion rate is $\alpha/2$,
not $\alpha$. At the $\alpha = 0.05$ default that is 2.5%.

### Thresholds

| Parameter | Default | Provenance |
|---|---|---|
| $\alpha = 0.05$ | Convention. **Not** a statistically derived threshold and not a regulatory requirement. Calibrate against the cost of a wrong promotion. |
| $N_{\text{min}} = 30$ | Rule of thumb. It is *not* a point at which Welch's test becomes exact — the test is exact under normality at any $n \geq 2$, and the $t$/normal gap this page measures persists well beyond $N=30$. Treat it as a floor for stability of $s^2$, not a guarantee. |
| Hard floor $N \geq 2$ | Structural: the $(n-1)$ sample variance and the Welch-Satterthwaite denominator are both undefined at $n = 1$. |

### Peeking

Continuous monitoring of a fixed-horizon test invalidates its $p$-value; the
canonical treatment, and the always-valid sequential alternative, is Johari,
Koomen, Pekelis & Walsh, *Always Valid Inference: Continuous Monitoring of A/B
Tests*, **Operations Research 70(3)**, 2022 (preprint
[arXiv:1512.04922](https://arxiv.org/abs/1512.04922)), which states that
frequentist $p$-values and confidence intervals "are wholly unreliable if users
endogenously choose samples sizes by continuously monitoring their tests". The
12.0% figure in the table above is measured here against this module's own
decision rule, not quoted from that paper. This module implements the
fixed-horizon test only; if you need to monitor continuously, you need
always-valid inference, not a lower $\alpha$.

## Regulatory context — EU/EEA only, and not a threshold source

**Scope.** The material below applies to investment firms engaged in
algorithmic trading under MiFID II (Directive 2014/65/EU) and Commission
Delegated Regulation (EU) 2017/589 ("RTS 6"). It does not apply to a US, UK,
Indian or Singaporean entity by default, and it prescribes **no** statistical
method, threshold or sample size. Confirm applicability to your own entity and
jurisdiction before treating any of it as binding.

**Why it touches this skill at all.** Promoting a challenger to champion is a
change to a live trading algorithm, which is what brings the testing and
change-control obligations into play — not the A/B test itself.

ESMA, *Supervisory Briefing on Algorithmic Trading in the EU*,
ESMA74-1505669079-10311, **26 February 2026**, is the most current supervisory
material on this. Note its own status: it is issued under Article 29(2) of the
ESMA Regulation and states that its content "is non-binding and not subject to
a 'comply or explain' mechanism". It is convergence guidance for national
competent authorities, not a rule.

Two points from it bear directly on how this module's output should be used.

- **A promotion is a material change.** ¶31 defines a material change or
  substantial update as "any modification that may alter the behaviour, risk
  profile, or compliance posture of an algorithm, algorithmic trading system or
  algorithmic trading strategy", and states that firms "are required to
  timestamp, approve, and record all material changes". Its non-exhaustive
  retest triggers explicitly include changes to logic or decision rules and to
  execution behaviour. Swapping the model that generates live signals is both.
- **Automated promotion loops are called out by name.** ¶30 warns that firms
  "should manage the risk that a series of minor or small changes due to
  recalibrations could accumulate over time, when uncontrolled or unchecked,
  into a material change in the model output without it being tested". An
  unsupervised pipeline that promotes whenever $p < 0.05$ is precisely that
  pattern. This is the reason `recommended_action` is advisory in this module
  and why the promotion decision is deliberately left outside it.

The briefing cites RTS 6 Articles 5, 6 and 7 for testing and Article 10 for
stress testing (its footnote 3), and Article 9 for the annual self-assessment
and validation process covering, among other things, a firm's "governance,
accountability and approval framework" (¶49). The authors of this skill verified
the ESMA briefing's reference, date, non-binding status and the quoted
paragraphs from the primary document; the **full text of the individual RTS 6
articles was not retrieved from a primary source** and is therefore not
paraphrased or relied on here beyond that citation.

## Sources

| Source | Used for |
|---|---|
| NIST/SEMATECH, *e-Handbook of Statistical Methods*, §1.3.5.3 — [itl.nist.gov](https://www.itl.nist.gov/div898/handbook/eda/section3/eda353.htm) | Welch $t$-statistic, Welch-Satterthwaite $\nu$, and the $t$ (not normal) reference distribution. |
| Johari, Koomen, Pekelis & Walsh (2022), *Always Valid Inference: Continuous Monitoring of A/B Tests*, Operations Research 70(3); [arXiv:1512.04922](https://arxiv.org/abs/1512.04922) | The peeking problem and its always-valid remedy. |
| ESMA74-1505669079-10311, *Supervisory Briefing on Algorithmic Trading in the EU*, 26 Feb 2026 — [esma.europa.eu](https://www.esma.europa.eu/sites/default/files/2026-02/ESMA74-1505669079-10311_Supervisory_Briefing_on_Algorithmic_Trading_in_the_EU.pdf) | Definition of a material change / substantial update (¶31); accumulated-recalibration risk (¶30); non-binding status (¶3); RTS 6 Article 9 self-assessment (¶49). |
| `scipy.stats.ttest_ind(equal_var=False)` | Independent cross-validation of $t$, $\nu$ and $p$; agreement to $1.1 \times 10^{-13}$ over 300 randomised cases. |
