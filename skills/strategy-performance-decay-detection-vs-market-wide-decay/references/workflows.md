# Workflows for Strategy Performance Decay Detection vs Market-Wide Decay

## 1. Return alignment

- Intersect the target and peer indices. Observations present in only one series are
  dropped and the count is recorded in `warnings`.
- Reject, do not repair: an interior `NaN`/`Inf` in the aligned window, duplicate index
  labels, an index that is not sorted ascending, and any return at or below $-100\%$.
  Each raises `DecayDiagnosticError`.
  - A dropped interior gap makes non-adjacent periods adjacent and shrinks the sample
    invisibly. A duplicate label turns the join into a partial cartesian product. An
    unsorted index makes the trailing window the wrong observations.
- Require at least `rolling_window_days` aligned observations; the diagnosis reads only
  the trailing window.

## 2. Sharpe estimation over the trailing window

- Deduct the risk-free rate **per period**: $r_f^{(p)} = (\text{annual \%}/100)/F$.
- $\text{Sharpe}_{\text{ann}} = \dfrac{\overline{e}}{\text{sd}(e)}\sqrt{F}$ where
  $e = r - r_f^{(p)}$ and $\text{sd}$ uses $\text{ddof}=1$.
- If either series is constant to floating-point resolution the Sharpe ratio is
  **undefined**, not zero. Return `INCONCLUSIVE` with `NaN` Sharpe ratios and a warning;
  never let a zero-volatility profitable strategy fall below the health threshold.

## 3. Sharpe-difference significance test

Jobson & Korkie (1981) with the Memmel (2003) correction, on per-period Sharpe ratios:

$$
z = \frac{\hat{Sh}_t - \hat{Sh}_p}{\sqrt{\hat\theta}},\qquad
\hat\theta = \frac{1}{T}\left[2 - 2\rho + \tfrac{1}{2}\left(\hat{Sh}_t^2 + \hat{Sh}_p^2 - 2\hat{Sh}_t\hat{Sh}_p\rho^2\right)\right]
$$

- $\rho$ is the sample correlation of the two return series and is the dominant term in
  $\hat\theta$. Omitting it leaves a quantity with no null distribution.
- Do **not** estimate the standard error from a history of overlapping rolling Sharpe
  ratios. Consecutive windows share all but one observation; their dispersion is an
  autocorrelation artifact. Measured against a true null that construction rejects at
  roughly twice its nominal rate.
- $\hat\theta$ collapses to zero only when the two series are effectively identical.
  Report $z$ and the p-value as `None` — never 0.0 — and classify `INCONCLUSIVE`.
- Report the one-sided p-value $\Phi(z)$ for $H_1: Sh_t < Sh_p$, so the result is
  auditable at levels other than the configured threshold.
- Below ~30 observations the asymptotics are visibly liberal; the engine still reports
  but attaches a warning.

## 4. Classification and action

Evaluate in this order:

| Condition | Classification | Action |
|---|---|---|
| $z$ not measurable | `INCONCLUSIVE` | `MONITOR_CLOSELY` |
| $z \le z_{\text{crit}}$ and peer healthy and **target impaired** | `IDIOSYNCRATIC_ALPHA_DECAY` | `DECOMMISSION_OR_RECODE` |
| target impaired and peer impaired | `MARKET_WIDE_REGIME_SHIFT` | `PAUSE_OR_REDUCE_RISK` |
| target healthy | `HEALTHY` | `MAINTAIN_TRADING` |
| target impaired, peer healthy, not significant | `INCONCLUSIVE` | `MONITOR_CLOSELY` |

- The `target impaired` condition on the idiosyncratic branch is load-bearing.
  Significance alone means only that the peers did better; a strategy above the health
  threshold is an allocation question, and the engine records that in `warnings` rather
  than recommending decommissioning.
- Joint impairment outranks significance: when the peer group is down too, the evidence
  does not support an idiosyncratic verdict, even if the target is significantly worse.

## 5. Acting on the report

1. Read `warnings` first. Alignment drops, small samples and undefined Sharpe ratios all
   qualify the classification.
2. Confirm the peer index is still the right comparator before acting on a change in
   classification — a benchmark swap invalidates the comparison.
3. Rule out execution causes (slippage, routing, fee changes) before concluding the
   signal decayed.
4. For anything approaching a decommissioning decision, re-test with a studentized
   time-series bootstrap (Ledoit & Wolf 2008); the closed-form statistic assumes
   i.i.d. normal returns and over-rejects when they are not.
5. Record `strategy_id`, `peer_benchmark_id`, `observations`, both Sharpe ratios, $z$,
   the p-value and the thresholds used. The classification is not reproducible without
   them.
