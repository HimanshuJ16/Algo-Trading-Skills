# Standards for Multi-Model Ensemble Weight Decay

No regulator sets a decay factor, a softmax temperature, or a weight floor.
Everything below is either an exact mathematical identity, a published empirical
convention with its source named, or a repository engineering default stated as
a default. Where a value is a choice rather than a result, it says so.

## Exact identities (not conventions)

| Quantity | Identity | Notes |
|---|---|---|
| EWMA recursion | $\bar{X}_t = \lambda \bar{X}_{t-1} + (1-\lambda) X_t$ | A convex combination only for $\lambda \in [0,1]$. Outside that range the recursion diverges; the engine rejects it. |
| Memory half-life | $t_{1/2} = \ln(0.5) / \ln(\lambda)$ | $\lambda{=}0.90 \to 6.5788$; $0.94 \to 11.2023$; $0.95 \to 13.5134$; $0.97 \to 22.7566$; $0.99 \to 68.9676$. Undefined at $\lambda \in \{0, 1\}$. |
| Effective window | $1/(1-\lambda)$ | The sum of the geometric weights. $\lambda{=}0.95 \to 20$ periods. |
| Softmax shift invariance | $\text{softmax}(s - c) = \text{softmax}(s)$ for any scalar $c$ | The shift factors out of numerator and denominator. Taking $c = \max_j s_j$ pins the largest term at $\exp(0)=1$, so the denominator is always $\ge 1$. |
| Two-model softmax | $w_A = \dfrac{e^{\beta(L_B - L_A)}}{1 + e^{\beta(L_B - L_A)}}$ | The logistic form. Used to check the engine against a closed form rather than against itself. |
| Seeding | first call $\Rightarrow \bar{X} = \lambda X + (1-\lambda) X = X$ | $\lambda$ has no observable effect until the second call. |

## Sourced conventions

| Parameter | Value | Source | Applicability |
|---|---|---|---|
| $\lambda$ (daily) | 0.94 | J.P. Morgan/Reuters, *RiskMetrics — Technical Document*, 4th ed. (1996), Appendix C | Fitted for **daily return volatility**, not for model forecast loss. Transferring it here is a defensible starting point, not a validated result. |
| $\lambda$ (monthly) | 0.97 | *ibid.* | Same caveat. |
| Softmax weighting | $w_i \propto \exp(-\eta L_i)$ | Freund & Schapire (1997), Hedge; Cesa-Bianchi & Lugosi, *Prediction, Learning, and Games* (2006), Ch. 2 | `temperature_beta` **is** the learning rate $\eta$. **Caveat:** published Hedge exponentiates a *cumulative* loss and its regret bounds are stated for that. This engine exponentiates a *discounted* loss — the appropriate variant under non-stationarity — so those bounds do not transfer unchanged. |
| Softmax max-shift | subtract $\max_j s_j$ | Goodfellow, Bengio & Courville, *Deep Learning* (2016), Ch. 4 "Numerical Computation" | Standard practice; the identity itself is exact and provable independently of the citation. |
| Information coefficient | $IR = IC \cdot \sqrt{\text{breadth}}$ | Grinold (1989); Grinold & Kahn, *Active Portfolio Management*, 2nd ed. (1999) | Defines IC as the correlation between forecast and realised return. The fundamental law is known to overstate achievable IR under its independence assumptions — it motivates the IC breaker, it does not calibrate it. |
| $\mathrm{SE}(IC)$ | $\approx 1/\sqrt{N-1}$ under $H_0: \rho = 0$ | Standard sampling theory for a Pearson correlation | At $N = 100$ names, $\approx 0.10$. This is why the breaker reads the **discounted** IC, not a single period's. |

## Repository defaults (choices, enforced as bounds only)

| Parameter | Default | Enforced bound | Rationale |
|---|---|---|---|
| `decay_factor_lambda` | 0.95 | $[0, 1]$, rejected outside | Half-life 13.51 periods; brackets the RiskMetrics daily/monthly anchors. The bound is mathematical (convexity); 0.95 itself is a default, not a mandate. |
| `temperature_beta` | 2.0 | finite and $> 0$ | $\beta \le 0$ inverts the softmax and awards the largest weight to the worst model. The bound is a correctness requirement; the value is a choice. |
| `min_weight_floor` | 0.05 | $[0, 1)$ **and** $< 1/M$ | At or above the equal share, every model is demotable even when all perform identically. The $1/M$ bound is structural; 0.05 suits a roster of roughly 3–15 models and must be lowered as $M$ grows. |
| `weighting_method` | `EXPONENTIAL_LOSS` | must be one of the two named constants | An unrecognised string previously fell through to IC behaviour. |
| `demote_on_negative_ic` | `True` | — | Applies under **both** weighting methods. |
| `min_days_active` | 1 (no-op) | $\ge 1$ | Opt-in warm-up guard. Any threshold above 1 is the caller's judgement about their own telemetry; this repository does not assert one. |
| `WEIGHT_PRECISION` | 6 dp | — | Reported active weights are residual-corrected to sum to exactly 1.0 at this precision under `math.fsum`. |

## Invariants the engine guarantees

1. Reported active weights sum to exactly $1.0$ under `math.fsum`. A naive
   left-to-right `sum()` may differ by one or two ULPs (~$2\times10^{-16}$) —
   IEEE-754 accumulation in the caller, not a residual in the allocation.
2. Every active weight is $\ge$ `min_weight_floor` after renormalisation. One
   demotion pass is provably sufficient: dividing by a sum $\le 1$ can only
   increase a surviving weight.
3. Every non-active weight is exactly $0.0$.
4. `active_model_count + demoted_model_count + pending_warmup_model_count
   == len(models)`. `PENDING_WARMUP` is counted apart from the two
   `DEMOTED_*` reasons: insufficient history is not evidence of failure.
5. No finite input produces a non-finite weight; non-finite *input* is rejected.
6. Identical inputs produce identical reports. The engine holds no state.
7. If no model is eligible for weight — every model failed a breaker, or every
   model is still warming up — the status is `ENSEMBLE_HALTED_ALL_DEMOTED`,
   every weight is $0.0$, and the event is logged at ERROR with a per-model
   reason. There is no equal-weight fallback.

## Stated limitations

- **Weights are computed per model with no notion of correlation.** Near-duplicate
  models collectively receive a multiple of the weight one independent model
  would get. Deduplicate the roster upstream.
- **Loss and IC are taken on trust.** The engine validates that they are finite
  and that the loss is non-negative. It cannot tell an out-of-sample loss from
  an in-sample one, and weighting on the latter allocates to the best overfitter.
- **The RiskMetrics $\lambda$ values were fitted for volatility**, not model
  forecast loss. No source is claimed for their optimality in this application.
- **No turnover or transaction-cost term.** A short half-life with a large $\beta$
  can rotate the book aggressively; the cost of that rotation is not modelled
  here. See `rebalancing-frequency-optimization-cost-vs-drift`.
- **`days_active` is a count supplied by the caller**, not a verified history
  length. The warm-up guard is only as honest as that field.
