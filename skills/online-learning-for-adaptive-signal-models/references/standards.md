# Standards for Online Learning of Adaptive Signal Models

## Engineering requirements

| Requirement | Standard | Source |
|---|---|---|
| Label horizon | A weight update MUST NOT be applied before the target's realisation time. `update()` refuses `label_ready_time > now`; `LabelHorizonBuffer` holds the sample until it is due. | Repository mandate; `lookahead-bias-elimination` |
| Gradient step stability | The per-update ratio $\eta\lVert x_t\rVert^2$ MUST stay below 2, and MUST be measured rather than assumed. NLMS enforces it structurally; LMS reports breaches. | Derived (below); Haykin, *Adaptive Filter Theory* |
| Non-finite input | A non-finite feature, target or resulting weight MUST raise before it can be installed. The L2 projection does **not** catch NaN. | Derived (below) |
| Weight bound | The weight vector MUST be projected onto $\lVert w\rVert_2 \le W_{\max}$ after every update. | Zinkevich (2003), projected online gradient descent |
| RLS forgetting | $\lambda \in (0,1]$; the effective memory $T_0 = 1/(1-\lambda)$ MUST be derived from the horizon being tracked. | MathWorks System Identification Toolbox |
| Covariance windup | Under poor excitation $P$ grows without bound at $\lambda < 1$; a bound MUST be enforced. This module freezes $P$ at a trace limit. | Vahidi, Stefanopoulou & Peng (2005); Åström & Wittenmark |
| Drift thresholds | Page-Hinkley `delta` and `threshold` carry the units of the monitored quantity and MUST be set explicitly. No defaults are supplied. | Repository mandate |
| Bounded memory | Retained state MUST NOT grow with the length of the stream. | Repository mandate |
| Determinism | An identical input sequence MUST produce identical weights. No unseeded randomness, no wall-clock reads. | Repository mandate |
| Reported statistics | Every figure in `OnlineModelAuditReport` MUST be computed from observations the caller supplied. No simulated or assumed values. | Repository mandate |

## Parameter guidance

These are **starting points with stated provenance**, not thresholds any authority
mandates. The only entry below that is a theorem rather than a convention is the
NLMS stability region.

| Parameter | Guidance | Basis |
|---|---|---|
| `learning_rate` ($\mu$, NLMS) | $0 < \mu < 2$; $\mu \approx 0.1 - 0.5$ trades tracking speed against gradient noise | Stability region is exact (below); the sub-range is convention |
| `learning_rate` ($\eta$, LMS) | Whatever keeps $\eta\lVert x\rVert^2$ well below 2 **on your features** | Derived; not transferable between instruments or feature sets |
| `forgetting_factor` ($\lambda$) | Typical choices lie in $[0.98, 0.995]$; choose via $T_0 = 1/(1-\lambda)$ | MathWorks System Identification Toolbox |
| `rls_initial_covariance` ($c$) | Large $c$ = diffuse prior = fast initial adaptation; equivalent to a ridge prior of weight $1/c$ whose influence decays as $\lambda^t$ | Standard RLS initialisation |
| `rls_max_covariance_trace` | Must exceed the initial trace $n \cdot c$, or the guard freezes $P$ before the first update — refused at construction | Derived |
| `max_weight_norm` ($W_{\max}$) | A property of your feature scaling and acceptable exposure, not a universal constant. There is no defensible default; the module's 10.0 is a placeholder to be replaced | Stated limitation |
| `l2_penalty` | Applies to the gradient rules only, as leakage $w \leftarrow (1-\eta\gamma)w + \ldots$. **Ignored by RLS** — a warning is logged | Leaky-LMS convention |
| Page-Hinkley `delta` | The per-observation error increase you are willing to tolerate | Must be set from your own error scale |
| Page-Hinkley `threshold` | The cumulative excess error that constitutes evidence of a change | Must be set from your own error scale |

## Derived results

**Gradient-step stability.** For the LMS update $w' = w + \eta e x$ with
$e = y - w^{\mathsf T}x$, the a posteriori error is

$$e_{\text{post}} = y - w'^{\mathsf T}x = e - \eta e \lVert x\rVert^2 = e\left(1 - \eta\lVert x\rVert^2\right)$$

so $|e_{\text{post}}| < |e|$ if and only if $0 < \eta\lVert x\rVert^2 < 2$. The NLMS
step $\mu/(\epsilon + \lVert x\rVert^2)$ makes that product equal
$\mu\lVert x\rVert^2/(\epsilon + \lVert x\rVert^2) \to \mu$, which is why the NLMS
stability region is stated as $0 < \mu < 2$ **independent of the signal
statistics**, while the LMS one is not. This is arithmetic, not a citation.

**Why the norm cap does not catch NaN.** IEEE-754 comparisons with NaN are false,
so `norm > max_norm` is `False` when `norm` is NaN and the projection is skipped.
Any weight that reaches NaN then stays NaN, and every subsequent prediction is
NaN — with no exception and no clipping. Rejection has to happen on input.

**Covariance windup.** With $x_t = 0$ the RLS gain is zero and the recursion
reduces to $P_t = P_{t-1}/\lambda$: the trace grows as $\lambda^{-t}$ with no data
arriving at all. Real markets supply the degenerate case less starkly — a quiet
session, a feature that stops varying — but the direction is the same.

## Verified sources

**Haykin, S. *Adaptive Filter Theory*. Prentice Hall / Pearson.** The standard
reference for LMS, NLMS and RLS. The NLMS update
$w(n+1) = w(n) + \dfrac{\mu}{\epsilon + \lVert x(n)\rVert^2} e(n) x(n)$ with
regularisation constant $\epsilon$ preventing division by zero, and the stability
region $0 < \mu < 2$ whose independence from the input statistics is NLMS's stated
advantage over LMS, are textbook results reproduced across the adaptive-filtering
literature.

**Page, E. S. (1954). "Continuous Inspection Schemes." *Biometrika* 41(1/2), 100–115.**
The origin of the cumulative-sum change detection scheme the Page-Hinkley test
implements. Cited by scikit-multiflow's `PageHinkley` as the method's reference.

**Gama, J., Žliobaitė, I., Bifet, A., Pechenizkiy, M. & Bouchachia, A. (2014).
"A Survey on Concept Drift Adaptation." *ACM Computing Surveys* 46(4), Article 44.**
<https://dl.acm.org/doi/10.1145/2523813> Surveys the Page-Hinkley test in its
streaming form: a cumulative variable aggregating each observation's deviation
from the running mean less a tolerance $\delta$, signalling when it exceeds its
running minimum by more than a threshold $\lambda$. Implemented as
`PageHinkleyDetector`.

**Vahidi, A., Stefanopoulou, A. & Peng, H. (2005). "Recursive least squares with
forgetting for online estimation of vehicle mass and road grade: theory and
experiments." *Vehicle System Dynamics* 43(1), 31–55.** States the covariance
windup problem directly: during poor excitation old information is continuously
forgotten while little new information arrives, and elements of the covariance
matrix become very large. Remedies discussed in this literature include
directional/subspace forgetting, covariance resetting, and trace-limited RLS
which switches the estimator off when the trace reaches a user-specified limit —
the approach taken here.

**MathWorks, System Identification Toolbox — Recursive Least Squares Estimator.**
<https://www.mathworks.com/help/ident/ref/recursiveleastsquaresestimator.html>
Forgetting factor range $(0, 1]$; "Suppose that the system remains approximately
constant over $T_0$ samples. You can choose $\lambda$ such that
$T_0 = 1/(1-\lambda)$"; "Typical choices of $\lambda$ are in the $[0.98\ 0.995]$
range"; $\lambda = 1$ corresponds to no forgetting.

**Zinkevich, M. (2003). "Online Convex Programming and Generalized Infinitesimal
Gradient Ascent." *ICML 2003*, 928–936.** The projection step used after each
update — apply the gradient, then project back onto a bounded convex set — is
projected online gradient descent. Cited for what the weight cap *is*; it is not
gradient clipping and not a per-component bound.

## Stated limitations

1. **Linear models only.** Every rule fits $\hat y = x^{\mathsf T}w$. Nothing here
   detects that the true relationship is non-linear, and the MAE can fall while
   the model fits a line through a curve.
2. **No intercept.** Predictions pass through the origin. Centre the target or
   append a constant feature; the module cannot tell which you intended.
3. **`is_converged` is not a significance test.** It is a point comparison of two
   MAEs, and it is noisy at small windows. For a bounded accuracy monitor wired to
   position sizing, use `model-staleness-detection`.
4. **Drift detection answers "did the error mean rise", not "why".** The
   staleness / covariate-shift / concept-drift separation is
   `concept-drift-vs-staleness-differentiation`.
5. **The horizon gate checks timestamps, not feature construction.** A feature
   built from a centred rolling window or a restated fundamental leaks past every
   timestamp check cleanly. See `feature-engineering-without-leakage`.
6. **RLS is $O(n^2)$ per update** in both time and memory. On a wide feature
   vector in a latency-sensitive path, measure before adopting it.
7. **Scalar (isotropic) forgetting only.** Directional and variable-rate
   forgetting, which bound the covariance without persistent excitation, are not
   implemented; the trace limit is the cruder remedy.
8. **`max_weight_norm = 10.0` is a placeholder.** It is not derived from anything
   and must be set from your own feature scaling and exposure limits.
9. **Single-threaded.** The model holds mutable state and is not safe to update
   from more than one thread.
10. **The Page-Hinkley mean is cumulative, and gets sluggish.** `PageHinkleyDetector`
    tracks the running mean over every observation since the last reset, so after
    a long stationary run a change has to move a heavily-anchored mean before the
    cumulative statistic climbs, and detection is late. Gama et al. discuss a
    forgetting-weighted variant of the mean for exactly this; it is **not**
    implemented here. Reset the detector on a schedule if long uninterrupted runs
    are expected.
