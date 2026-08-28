# Workflows — strategy-performance-attribution-vs-market-beta

Deep procedure for `StrategyPerformanceAttributionEngine.analyze_attribution`. The
definitions and their sources are in `references/standards.md`; the sign-off gates are
in `assets/checklist.md`.

## 1. Assemble the factor data

1. **Strategy returns** — simple periodic returns as decimal fractions, net of fees,
   financing and borrow. Gross returns inflate alpha by exactly the cost you removed.
2. **Benchmark returns** — the market proxy the strategy actually trades against, on
   the same calendar. A US small-cap strategy regressed on the S&P 500 loads its size
   exposure into the residual and reports it as alpha.
3. **Fama-French factors** (optional) — from Ken French's data library or a vendor
   replication. Two conversions are mandatory before use:
   - **Divide by 100.** French's files are in percent.
   - **Flag the market column.** `Mkt-RF` is already an excess return; pass it with
     `market_returns_are_excess=True`. If instead you build a total-return market series
     yourself, leave the flag `False` and let the engine subtract the risk-free rate.
   - Leave SMB and HML alone. They are zero-investment spreads.
4. **Risk-free rate** — an annual percentage. It is de-annualized geometrically,
   $(1+R_f)^{1/P}-1$, matching how French's `RF` column is constructed. Using $R_f/P$
   instead overstates the periodic hurdle by roughly 12 bp a year at a 5% rate.

## 2. Align, and inspect what alignment cost you

The engine joins every supplied series on its index and drops rows with any missing
value. Two consequences deserve an explicit check:

- **A short factor truncates the whole regression.** If SMB starts three years after
  the strategy, the entire fit — market beta included — is estimated on the shorter
  overlap. Compare `observations` and `dropped_observations` against what you expected.
- **Alignment is by index label, not by position.** Two series of equal length on
  mismatched calendars will align on their union and drop nearly everything; a small
  `observations` count is the symptom.

Hard failures (raised, not warned): duplicate index labels, infinities, non-numeric
values, factors collinear with the market, and fewer than $k+2$ aligned rows. A sample
shorter than one year at the configured frequency sets `insufficient_history_warning`
and appends a warning to `audit_notes` — it is not blocked, because monitoring a young
strategy is legitimate; publishing its annualized alpha is not.

## 3. Choose the model

- **CAPM** (`market_returns` only) answers "is this more than levered market
  exposure?". Use it when the strategy is not an equity-style strategy or when no
  credible style factors exist for it.
- **Fama-French 3-factor** (add `smb_returns`, `hml_returns`) answers "is this more than
  levered market exposure *plus* a cheaply replicable size or value tilt?". Use it for
  equity strategies. A strategy that looks alpha-generating under CAPM and loses its
  alpha under FF3 was being paid for a style tilt, not skill.

Adding a factor never *creates* alpha; it can only reassign return from the intercept to
the new loading. If alpha is unchanged after adding SMB and HML, the strategy genuinely
has no style tilt.

## 4. Choose the standard errors

| Situation | Estimator |
|---|---|
| Daily returns, liquid instruments, non-overlapping positions | `"ols"` (default) |
| Illiquid or stale-marked assets, overlapping holding periods, trend/momentum, monthly data with persistent residuals | `"hac"` |

`"hac"` changes only the standard errors, $t$-statistics and $p$-values; the
coefficients are identical. It requires a chronologically sorted index and raises
otherwise, because the Bartlett kernel weights observations by lag distance.

The default lag $\lfloor 4(n/100)^{2/9}\rfloor$ is a generic rule of thumb. When the
dependence horizon is known — an overlapping 21-day holding period, say — set
`hac_lags` to it explicitly.

**Report which estimator you used.** `standard_error_type` and `hac_lags` are on the
report precisely so an alpha claim can be reproduced.

## 5. Read the inference correctly

- `alpha_t_statistic` vs `t_critical_value`, not vs 1.96. The critical value is the
  exact two-sided Student-$t$ quantile at `significance_level` with `degrees_of_freedom`
  $= n-k$. At $n=252$ daily it is 1.9695; at $n=60$ monthly it is 2.0017.
- `alpha_p_value` is two-sided. It assumes normally distributed residuals — Jensen's own
  caveat. Treat a $p$ of 0.048 as borderline, not as a decision.
- `alpha_t_statistic is None` means the residual variance is zero (perfect in-sample
  fit — synthetic or degenerate data), so no sampling distribution exists.
  `undefined_metrics` states the reason. It is not "not significant".
- `is_true_alpha_significant` is a single 5% test on a single strategy. Screening
  candidates requires a multiple-testing correction that this engine does not apply.

## 6. Reconcile the decomposition before quoting it

$$\text{total} = \underbrace{R_f}_{\texttt{risk\_free\_contribution\_pct}} + \underbrace{\alpha}_{\texttt{annualized\_jensens\_alpha\_pct}} + \sum_i \underbrace{\beta_i \bar{x_i}}_{\texttt{return\_contribution\_pct}}$$

Assert `abs(unexplained_residual_pct) < 1e-8` before using any figure. It is computed
from unrounded quantities and closes exactly by construction; a non-zero value means the
implementation is broken, not that the strategy has unexplained return.

Reconstructing the total from the published (rounded) fields agrees only to about
0.01 percentage points — that is rounding, not residual.

## 7. Turn the split into a decision

| Pattern | Reading | Action |
|---|---|---|
| Large total return, $\beta_M > 1.2$, alpha insignificant | A levered index fund | Price as beta; do not charge an active fee |
| Alpha significant, $\beta_S$ large and significant | Small-cap tilt plus residual skill | Replicate the tilt cheaply; size on the residual only |
| Alpha significant under CAPM, insignificant under FF3 | The "alpha" was a style tilt | Re-benchmark against the style |
| Alpha significant under OLS, insignificant under HAC | Serially correlated residuals inflated the OLS $t$ | Treat the alpha as unproven; extend the sample |
| Alpha significant but `insufficient_history_warning` set | Too short to annualize | Keep monitoring; do not publish the annualized figure |
| High $R^2$ **and** significant alpha | Both are true; they are separate questions | Do not discount the alpha because of the $R^2$ |

## 8. Re-run on a schedule and on a split window

A single full-sample regression hides regime change. Re-estimate on rolling or split
windows and compare the betas: a beta that moves materially between halves invalidates
the single-window alpha, because the model assumed a constant exposure the strategy did
not have. Persistent alpha decay across windows is a different diagnosis — see
`strategy-performance-decay-detection-vs-market-wide-decay`.
