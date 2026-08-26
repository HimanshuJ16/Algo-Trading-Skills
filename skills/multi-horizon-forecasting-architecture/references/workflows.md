# Workflows for Multi-Horizon Forecasting

## 1. Prediction ingestion

Collect exactly one forecast per forward horizon $\tau_1 < \dots < \tau_K$, each carrying
`predicted_return` (the return expected over that whole horizon), `ic_score` (measured
out-of-sample *at that horizon*), and `confidence`.

Validate at the boundary, before anything reaches a position size:

- `horizon_steps` is an integer $\ge 1$, and every prediction uses the **same base unit**
  (minutes, seconds, bars). Only the ratios $\tau_k/\tau_\star$ are used, so a mixed-unit
  call is silently wrong rather than loudly wrong.
- No duplicate horizons. Two models forecasting the same horizon must be aggregated
  upstream; combining them here double-counts that horizon's weight.
- `predicted_return`, `ic_score`, `confidence` are finite. One NaN otherwise propagates
  straight through the weighted sum into the composite.
- $IC_k \in [-1, 1]$ — it is a correlation. A value outside that range means the
  reported IC is not an IC.
- $c_k \in [0, 1]$.

## 2. Horizon scale normalization

Choose the target horizon $\tau_\star$ — by default the shortest horizon supplied, which is
normally the horizon actually traded and re-evaluated. $\tau_\star$ fixes the units of the
composite, of the rescaled forecasts, and of the conflict threshold.

Rescale each forecast:

$$\tilde{y}_k = \hat{y}_{\tau_k}\cdot\frac{\sigma(\tau_\star)}{\sigma(\tau_k)}$$

- **`EXPLICIT_VOL`** — supply the measured $\sigma(\tau_k)$ for every horizon including
  $\tau_\star$. Makes no distributional assumption. Required when returns are autocorrelated,
  heteroscedastic, or jump-prone.
- **`SQRT_TIME`** — the factor becomes $\sqrt{\tau_\star/\tau_k}$. Convenient, and exact only
  under iid-type conditions; it understates long-horizon risk under jumps, so the long
  end of the horizon set is the part most likely to be misweighted.
- **`NONE`** — pass through unchanged. Only correct when normalization already happened
  upstream; otherwise this is the naive-averaging defect.

Worked example ($\tau_\star = 5$, $\tau_2 = 45$, `SQRT_TIME`): $\sigma$ ratio is
$\sqrt{45/5} = 3$, so a $+90$ bps 45-step forecast enters the blend as $+30$ bps.
Equal-weighted with a $+10$ bps 5-step forecast the composite is $+20$ bps — against
$+50$ bps if the raw numbers were averaged.

## 3. Horizon weighting

| Scheme | Weight |
|---|---|
| `IC_WEIGHTED` | $w_k = \max(0, IC_k)\cdot c_k$ |
| `INVERSE_HORIZON_SQRT` | $w_k = c_k/\sqrt{\tau_k}$ |
| `EQUAL` | $w_k = 1$ |

Normalize $\bar{w}_k = w_k/\sum_j w_j$.

Decision point: **if $\sum_j w_j = 0$, stop.** Under `IC_WEIGHTED` that means every
horizon has non-positive measured skill (or zero confidence). Do not fall back to equal
weighting — zero the composite, return `NO_VALID_HORIZON_WEIGHTS`, log a WARNING, and
treat it as a model-set problem rather than a signal.

Reject an unrecognized scheme outright. A misspelling that quietly degrades to equal
weighting changes the traded signal with no error and no log line.

## 4. Composite synthesis

$$\alpha = \sum_k \bar{w}_k\,\tilde{y}_k \qquad\text{(an expected return over } \tau_\star\text{)}$$

$$\text{score} = \alpha / \sigma(\tau_\star)$$

The score is the dimensionless side of Grinold's $\alpha = \sigma \cdot IC \cdot
\text{score}$. Under `SQRT_TIME` the base-step volatility is unknown, so the divisor is
$\sqrt{\tau_\star}$ and the score is comparable only across calls sharing the same base step.

## 5. Consensus and conflict arbitration

Report two consensus measures, because they answer different questions:

- **Head-count consensus** — the share of directionally non-flat horizons agreeing on
  sign. Treats a $1\%$-weight horizon and a $90\%$-weight horizon alike.
- **Weight-weighted consensus** — the same split measured by normalized weight. A 2-vs-2
  head-count tie where the dissenters hold $5\%$ of the weight is a $95\%$ agreement.

If no horizon has a directional view, report $0\%$ on both. Reporting $100\%$ consensus
on an all-flat forecast set invites a downstream sizer to size up on nothing.

Flag a short-vs-long conflict when, **after rescaling**:

$$\operatorname{sign}(\tilde{y}_1) \neq \operatorname{sign}(\tilde{y}_K),\quad
|\tilde{y}_1| \ge \text{threshold},\quad |\tilde{y}_K| \ge \text{threshold}$$

Only the extreme pair is compared; disagreement among interior horizons shows up in the
consensus measures instead. Comparing after rescaling is what makes a single absolute
threshold meaningful — against raw forecasts the same threshold is trivially cleared by
the long horizon and nearly unreachable by the short one, so short-end conflicts go
undetected.

Then apply exactly one policy, and record which fired:

| Policy | Effect | Status |
|---|---|---|
| `REPORT_ONLY` | Composite untouched | `FORECAST_SYNTHESIZED_SUCCESS` |
| `DAMPEN` | $\alpha \leftarrow \alpha \cdot \text{damping}$ | `FORECAST_DAMPENED_ON_CONFLICT` |
| `SUPPRESS` | $\alpha \leftarrow 0$ | `FORECAST_SUPPRESSED_ON_CONFLICT` |
| `DEFER_LONG` | $\alpha \leftarrow \tilde{y}_K$ | `FORECAST_DEFERRED_TO_LONG_HORIZON` |

The damping factor is a policy choice, not a calibrated constant. Derive it from
conflict-conditional realized performance for the specific model set; the $0.5$ default
is a placeholder, not a recommendation.

## 6. Audit report

Emit `MultiHorizonForecastReport` with the composite, the score, the target horizon,
normalized weights, the rescaled per-horizon forecasts, both consensus measures, the
conflict flag, the scaling mode, the weighting scheme, and the status. The status is
what lets a downstream consumer tell a clean signal from a damped or suppressed one —
never collapse it to a boolean success flag.
