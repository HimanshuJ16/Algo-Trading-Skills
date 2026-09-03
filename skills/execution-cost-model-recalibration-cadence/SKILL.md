---
name: execution-cost-model-recalibration-cadence
description: >-
  Use when a pre-trade cost model feeds sizing or venue choice and you must know whether
  it still tracks reality; audits its error and systematic bias against realised
  shortfall and refits the spread and impact terms.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: execution-algorithms
  tags: execution-cost-model, recalibration-cadence, tca, tracking-error, rmse, prediction-bias, square-root-impact-model
  brokers_frameworks: "Square-Root Impact Model; TCA Governance; Ordinary Least Squares; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill in quantitative execution research, Transaction Cost Analysis (TCA), and portfolio construction where a pre-trade cost model feeds sizing, venue selection, or algo parameter choice. The audited model is the standard two-term decomposition

$$\text{IS}_{\text{pred}} = \eta \cdot \text{Spread}_{\text{bps}} + \gamma \cdot \sigma_{\text{bps}} \cdot \sqrt{\text{Qty} / \text{ADV}}$$

whose second term is the square-root impact law $I(Q) = Y \sigma \sqrt{Q/V}$ with $Y \sim 0.5\text{–}1.0$ (Tóth et al. 2011; Almgren et al. 2005). As market regimes shift — volatility spikes, tick-size regime changes, fee-schedule updates, venue mix changes — the fitted coefficients go stale, and the model develops tracking error ($\text{RMSE}$) and systematic bias ($\bar{\epsilon} \ne 0$) against realized IS. This module implements the **trigger arm** of a recalibration cadence: measure the drift, decide whether it warrants a refit, and produce refitted coefficients only when the sample can actually support them.

## When NOT to Use

- **As the whole cadence.** This is the trigger arm only. The calendar arm — the scheduled weekly/monthly review, and the annual validation floor RTS 6 Art. 9 places on EU algorithmic trading firms — is a scheduler concern and is not implemented here.
- **To promote parameters straight to production.** The refit is scored **in-sample**, on the very trades that triggered it. `post_refit_rmse_bps` is an optimistic lower bound on live tracking error, not a forecast of it. Validate on a held-out or subsequent sample first.
- **To diagnose *why* the model drifted.** A tripped threshold is a symptom. If the error came from a venue outage, a fee change, or a handful of outlier parents, refitting bakes that event into $\eta$ and $\gamma$. Screen the sample before refitting; use `execution-slippage-attribution-timing-vs-sizing` to decompose where the shortfall actually came from.
- **On a sample that does not span order sizes and spread regimes.** If every trade has near-identical spread and participation, the two regressors are collinear and $\eta$ and $\gamma$ are not separately identifiable — the combined prediction can look fine while each coefficient is arbitrary. The engine refuses such a fit rather than returning it.
- **On incomplete executions.** The engine treats its input as an unordered sample of *completed* parent orders. Working or partially-filled orders leak incomplete outcomes into the fit.
- **As an optimal-execution scheduler.** This calibrates a *cost estimate*; it does not produce a trade schedule. For the schedule, see `execution-algo-twap-vwap-slicing` and `implementation-shortfall-minimization`.

## Prerequisites

- Trade execution history of **completed** parent orders: `order_qty`, `adv_shares`, `spread_bps`, `volatility_daily_pct`, `realized_is_bps`.
- **Units contract.** The square-root law is dimensionally homogeneous — $\sigma$ must be in the same relative unit as the cost it predicts. This module works in bps, so `volatility_daily_pct` is **percent** ($1.5$ means $1.5\%$/day) and is converted internally to bps ($\times 100$). Supplying a decimal fraction ($0.015$) understates the impact term by $100\times$.
- Active model parameters ($\eta_{\text{active}}, \gamma_{\text{active}}$).
- Governance configuration: tracking-error limit, bias limit, minimum refit sample size. These are **defaults, not industry standards** — see `references/standards.md`.

## Workflow

1. **Validate the sample before measuring anything.**
   - **Decision point — reject non-finite values; never let them reach the metrics.** A single `NaN` in `realized_is_bps` propagates through the mean into both RMSE and bias, and every `nan > threshold` comparison evaluates False. Unvalidated, corrupt data reports the model as *stable* and silently suppresses the recalibration it should have triggered.
   - Reject non-positive `order_qty` and `adv_shares` (a zero ADV is a division by zero, a negative quantity a domain error on the square root) and implausible daily volatility, which is nearly always a units mistake.

2. **Model Performance Audit** — compute against the *active* parameters:
   - Tracking error: $\text{RMSE} = \sqrt{\frac{1}{N} \sum (\text{IS}_{\text{realized}} - \text{IS}_{\text{predicted}})^2}$
   - Systematic bias: $\bar{\epsilon} = \frac{1}{N} \sum (\text{IS}_{\text{realized}} - \text{IS}_{\text{predicted}})$, signed so **positive means the model under-predicts cost**.
   - **Decision point — compare thresholds on the unrounded metrics.** Rounding to display precision before the comparison creates a dead band: a bias of $+1.502$ bps rounds to $1.50$ and then fails a $|\bar{\epsilon}| > 1.50$ test. Round only when writing the report.

3. **Recalibration Trigger Audit**: $\text{RMSE} > \text{RMSE}_{\max}$ **OR** $|\bar{\epsilon}| > |\bar{\epsilon}|_{\max} \implies$ the model has drifted.

4. **Sample-sufficiency gate** — a breach is not automatically a refit.
   - **Decision point — if $N < N_{\min}$, defer.** Emit `RECALIBRATION_DEFERRED_INSUFFICIENT_SAMPLE`, retain the active parameters, and re-audit when the sample grows. Refitting a two-coefficient model on a handful of trades fits noise, and unstable cost coefficients destabilise every downstream portfolio optimisation that consumes them.

5. **Parameter Least-Squares Refitting** — solve the no-intercept normal equations in closed form for
   $$\text{IS}_{\text{realized}} = \eta \cdot \text{Spread} + \gamma \cdot \sigma \cdot \sqrt{\text{Qty}/\text{ADV}}$$
   - No intercept is fitted: a zero-size order in a zero-spread market has zero modelled cost, and a free intercept would absorb size-dependent impact into a constant.
   - The active parameters are **not** an input to the fit. Least squares finds the sample optimum; seeding it with the incumbent values would only bias the result toward a model the audit has already rejected.
   - **Decision point — check the design conditioning before trusting the coefficients.** The engine reports $\det/(S_{11}S_{22})$, which equals $1 - \cos^2$ of the angle between the two regressor columns ($1.0$ orthogonal, $0.0$ collinear). Below the floor, escalate to `RECALIBRATION_REQUIRED_MANUAL_REVIEW` rather than shipping arbitrary coefficients.
   - **Decision point — a negative fitted coefficient is not a small number, it is a wrong sign.** $\eta < 0$ or $\gamma < 0$ implies wider spreads or larger orders *reduce* cost. Withhold the recommendation and investigate the sample.

6. **Validate before promotion**: the report carries `post_refit_rmse_bps` and `post_refit_bias_bps`, both **in-sample**. Treat them as a sanity floor, not evidence the new parameters will hold live. Promote through the same versioning and rollback path as any other model change (`model-versioning-and-rollback`).

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Silently auditing corrupt data**: a `NaN` realized IS makes RMSE and bias `NaN`, and `nan > limit` is False on every threshold — so the worst possible data produces the most reassuring verdict, `MODEL_PARAMETER_STABLE`, and no recalibration is raised. Reject non-finite inputs at ingestion.
- **Over-recalibrating on transient noise**: refitting daily on a handful of trades produces high parameter variance and unstable portfolio optimisation. Gate the refit on sample size, not just on a threshold breach.
- **Rescaling instead of refitting**: multiplying both coefficients by a single ratio of mean realized to mean predicted cost cannot separate $\eta$ from $\gamma$ — it preserves whatever ratio the seed had, no matter what the data says, so a pure impact-regime shift silently reprices spread costs as well. A two-regressor least-squares solve is what "refit" has to mean here.
- **Mismatched volatility units**: the square-root law needs $\sigma$ in the same relative unit as the predicted cost. Feeding a decimal fraction where percent is expected shrinks the impact term $100\times$, making a 10%-of-ADV order look like a sub-bps execution and pushing all the fitted cost into the spread coefficient.
- **Ignoring systematic prediction bias**: tracking correlation while a constant under-prediction ($\bar{\epsilon} = +4.0$ bps) persists lets portfolio managers systematically underestimate trading costs on every order.
- **Trusting an in-sample refit**: the refit is scored on the trades that triggered it, so its RMSE is guaranteed no worse than the incumbent's — that is arithmetic, not evidence. Only out-of-sample performance is evidence.
- **Refitting through a one-off event**: a venue outage or fee-schedule change inflates realized cost for a specific reason. Recalibrating on that window bakes a transient into the standing model.
- **Using outdated historical trade windows**: training on calm, low-volatility history during a crash regime — or the reverse — produces a model calibrated to a regime that is no longer running.
- **Refitting on a sample with no design variation**: all-similar orders make the spread and impact regressors collinear; the fit "succeeds", the combined prediction looks reasonable, and each coefficient is individually meaningless.

## Verification

- Instantiate `ExecutionCostModelRecalibrationEngine()` with active parameters $\eta = 0.5$, $\gamma = 1.0$.
- **Prediction units**: for a 9,000-share order against 100,000 ADV ($\sqrt{0.09} = 0.3$) at a 2.0 bps spread and $1.5\%$/day volatility, `predict_slippage_bps` must return $0.5 \times 2.0 + 1.0 \times 150 \times 0.3 = 46.0$ bps.
- **Scenario 1 — stable**: 60 trades generated exactly at the active parameters $\implies$ RMSE and bias both $0$, status `MODEL_PARAMETER_STABLE`, no recommended parameters.
- **Scenario 2 — regime shift**: 60 trades generated at $\gamma = 2.0$ against an active $\gamma = 1.0$ $\implies$ status `RECALIBRATION_RECOMMENDED`, positive bias above the limit, and refitted $(\eta^*, \gamma^*) = (0.5, 2.0)$ recovered exactly.
- **Scenario 3 — identifiability**: data generated at $(\eta, \gamma) = (0.2, 3.0)$ must refit to that ratio, not to the ratio of whatever seed the fitter started from.
- **Scenario 4 — governance gates**: the same breach observed over 3 trades $\implies$ `RECALIBRATION_DEFERRED_INSUFFICIENT_SAMPLE` with no recommended parameters; an all-identical sample $\implies$ `RECALIBRATION_REQUIRED_MANUAL_REVIEW` on collinearity; a sample fitting to $\eta < 0$ $\implies$ `RECALIBRATION_REQUIRED_MANUAL_REVIEW` with the recommendation withheld.
- **Least-squares property checks**: residuals orthogonal to both regressor columns, and `post_refit_rmse_bps` never exceeding the active RMSE.
- **Negative checks**: empty history, `NaN`/`inf` realized IS, zero or negative ADV, negative quantity, negative spread or volatility, and implausible volatility must each raise.
- Run `python -m unittest discover -s skills/execution-cost-model-recalibration-cadence/scripts`.

## Related Skills

- `execution-algo-parameter-optimization-via-backtest`
- `execution-slippage-attribution-timing-vs-sizing`
- `transaction-cost-analysis-tca-integration`
- `model-versioning-and-rollback`
- `model-staleness-detection`
