# Workflows — execution-cost-model-recalibration-cadence

Deep procedure for the **trigger arm** of a cost-model recalibration cadence. The
calendar arm (scheduled review, and the RTS 6 Art. 9 annual validation floor for EU
algorithmic trading firms) is a scheduler concern — see `references/standards.md`.

## 1. Trade sample assembly

Assemble **completed** parent orders only. For each, record `order_qty`, `adv_shares`,
`spread_bps`, `volatility_daily_pct`, and the `realized_is_bps` measured against the
same benchmark the model was built to predict.

- Mixing benchmarks (arrival price for some trades, decision price for others) makes the
  residual a mixture of two different quantities and the fit meaningless.
- Working or partially-filled orders leak incomplete outcomes into the fit. Exclude them.
- Deduplicate. A parent counted twice is weighted twice in the least-squares objective.
- Fix the window explicitly and record it. "Recent trades" is not a specification, and the
  window choice is the single largest discretionary input to the refit.

**Units contract**: `volatility_daily_pct` is **percent** ($1.5$ = $1.5\%$/day). The
square-root law is dimensionally homogeneous, so $\sigma$ must be in the same relative
unit as the predicted cost; the engine converts percent to bps internally.

## 2. Validation gate

Reject before measuring, not after:

| Condition | Why it must raise |
|---|---|
| Non-finite `realized_is_bps` | `NaN` propagates through the mean into RMSE and bias; `nan > limit` is False on every threshold, so corrupt data reports `MODEL_PARAMETER_STABLE` and suppresses the recalibration. |
| `adv_shares <= 0` | Division by zero in the participation ratio. |
| `order_qty <= 0` | Domain error on $\sqrt{\text{Qty}/\text{ADV}}$. |
| Negative `spread_bps` or `volatility_daily_pct` | Not physical; would contribute a negative-cost regressor. |
| `volatility_daily_pct` above the plausibility ceiling | Almost always a units mistake (bps or annualized figure in the percent field). |

## 3. Error and bias audit

Against the **active** parameters:

$$\text{RMSE} = \sqrt{\frac{1}{N}\sum_i \epsilon_i^2}, \qquad
\bar{\epsilon} = \frac{1}{N}\sum_i \epsilon_i, \qquad
\epsilon_i = \text{IS}^{\text{realized}}_i - \text{IS}^{\text{pred}}_i$$

- Sign convention: **positive bias means the model under-predicts cost** — the expensive
  direction, since it makes trades look cheaper than they are.
- The two metrics answer different questions. RMSE is dispersion: per-trade error is too
  noisy to act on. Bias is level: the model is wrong in a consistent direction. A model
  can pass one and fail the other, which is why the trigger is an OR.
- Compare against the thresholds using the **unrounded** metrics. Rounding to display
  precision first creates a dead band — a bias of $+1.502$ bps rounds to $1.50$ and then
  fails a $> 1.50$ test. Round only when writing the report.

## 4. Trigger evaluation and governance gates

```
breach = (RMSE > rmse_limit) OR (|bias| > bias_limit)

if not breach                      -> MODEL_PARAMETER_STABLE
elif N < min_sample                -> RECALIBRATION_DEFERRED_INSUFFICIENT_SAMPLE
elif fit not well-posed            -> RECALIBRATION_REQUIRED_MANUAL_REVIEW
else                               -> RECALIBRATION_RECOMMENDED
```

A breach is a *finding*, not a mandate to refit. The sample gate is the direct control on
this skill's headline pitfall: refitting a two-coefficient model on a handful of trades
fits noise, and unstable cost coefficients destabilise every portfolio optimisation
downstream. When the refit is deferred, the breach is still reported —
`is_recalibration_triggered` stays true — so the finding is not lost.

## 5. Least-squares refitting

Solve the no-intercept normal equations in closed form, with
$x_1 = \text{Spread}_{\text{bps}}$ and $x_2 = \sigma_{\text{bps}}\sqrt{\text{Qty}/\text{ADV}}$:

$$\begin{bmatrix} S_{11} & S_{12} \\ S_{12} & S_{22} \end{bmatrix}
\begin{bmatrix} \eta \\ \gamma \end{bmatrix} =
\begin{bmatrix} T_1 \\ T_2 \end{bmatrix}, \qquad
S_{jk} = \sum_i x_{ji} x_{ki}, \quad T_j = \sum_i x_{ji} y_i$$

$$\eta^* = \frac{S_{22}T_1 - S_{12}T_2}{\det}, \qquad
\gamma^* = \frac{S_{11}T_2 - S_{12}T_1}{\det}, \qquad
\det = S_{11}S_{22} - S_{12}^2$$

Design notes:

- **No intercept.** A zero-size order in a zero-spread market has zero modelled cost, and
  a free intercept would absorb size-dependent impact into a constant that does not scale
  with order size.
- **The active parameters are not an input.** Least squares finds the sample optimum;
  seeding it would only bias the result toward a model the audit has already rejected.
- **Rescaling is not refitting.** Multiplying both coefficients by a single ratio of mean
  realized to mean predicted cost preserves the seed's $\eta:\gamma$ ratio no matter what
  the data says, so an impact-only regime shift silently reprices spread costs too.

## 6. Fit-quality gates

| Check | Threshold | Action on failure |
|---|---|---|
| $S_{11} > 0$ and $S_{22} > 0$ | — | Degenerate design (all-zero spread, or zero volatility/participation): reject. |
| Conditioning $\det/(S_{11}S_{22})$ | $\ge$ `min_design_conditioning` | Near-collinear: $\eta$ and $\gamma$ are not separately identifiable even though the combined prediction may look fine. Reject and widen the sample across order sizes and spread regimes. |
| $\eta^* \ge 0$ and $\gamma^* \ge 0$ | — | A negative coefficient implies wider spreads or larger orders *reduce* cost. Withhold from production and investigate the sample. |
| In-sample RMSE not worse than the incumbent's | — | Analytically unreachable for a correct solve; reaching it means the solve was numerically unsound. Escalate. |

## 7. Validation before promotion

`post_refit_rmse_bps` and `post_refit_bias_bps` are **in-sample**. Least squares minimises
in-sample squared error by construction, so the refit is *guaranteed* to score no worse
than the incumbent — arithmetic, not evidence.

Before promoting $(\eta^*, \gamma^*)$:

1. Score them on a **held-out or subsequent** trade sample not used in the fit.
2. Compare the coefficient magnitudes against the literature range — the square-root
   prefactor $Y$ is reported at order $0.5\text{–}1.0$, and $\eta \approx 0.5$ corresponds
   to paying half the quoted spread. A $\gamma$ far outside that range is a signal to
   re-examine the sample, not a discovery.
3. Confirm the drift was a regime change, not a one-off event (venue outage, fee-schedule
   change, a few outlier parents) that the refit would bake into the standing model.
4. Version the parameter change and keep a rollback path — see
   `model-versioning-and-rollback`.
5. Record the sample window, the trigger metrics, the conditioning, and the approver, so
   the change is reconstructible at review time.
