---
name: strategy-performance-decay-detection-vs-market-wide-decay
description: >-
  Use when a live Sharpe has fallen and the remediation depends on whether this
  strategy's own edge decayed or the whole peer group is impaired, testing the strategy
  against its cohort rather than against zero.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: portfolio-multi-strategy
  tags: risk-management, performance-decay, alpha-decay, regime-shift, peer-benchmark, sharpe-ratio-inference, jobson-korkie-memmel
  brokers_frameworks: "Jobson-Korkie/Memmel Sharpe Difference Test; NumPy; pandas"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this skill when a live strategy's realized Sharpe ratio has fallen and the remediation decision depends on *why*. The same drawdown has two incompatible causes: **idiosyncratic alpha decay**, where the edge has been arbitraged away or crowded out while the peer group carried on (decommission or recode), and a **market-wide regime shift**, where the whole asset class or strategy family is impaired (pause, cut allocation, wait). Acting on the wrong one is expensive in both directions — retiring a sound strategy in a bad quarter destroys capacity you cannot rebuild, and funding a dead one burns capital indefinitely.

The engine takes the target's returns and a peer benchmark index over a trailing window, computes both annualized Sharpe ratios, and tests the *difference* for significance with a statistic that has a defined null distribution. Peer health then discriminates between the two causes: significant relative underperformance **against healthy peers** is idiosyncratic; joint impairment is systematic.

## When NOT to Use

- **As the significance test of record on real return series.** The Jobson-Korkie/Memmel statistic assumes i.i.d. bivariate *normal* returns. Ledoit and Wolf (2008) show it over-rejects under heavy tails or serial correlation — both routine in strategy returns, and severe for smoothed or illiquid marks. Treat a marginal z as the trigger for a studentized time-series bootstrap, not as the decommissioning decision itself.
- **Without a defensible peer benchmark.** The classification is only as good as the peer index. Benchmarking a market-neutral stat-arb book against a long-only equity index manufactures "idiosyncratic decay" every time the index rallies. Choosing the benchmark is `benchmark-selection-for-strategy-evaluation`; this skill only measures against the one you supply.
- **On intraday bars while leaving `periods_per_year` at 252.** The default is daily. Frequency cannot be inferred from a list of floats — set it, or every Sharpe ratio reported is wrong by the square root of the frequency mismatch.
- **As a live regime signal or a risk control.** This is a periodic governance diagnostic on realized returns, not a real-time circuit breaker. Drawdown enforcement belongs to `kill-switch-and-drawdown-circuit-breakers`; live regime routing to `regime-detection-for-strategy-switching`.
- **To separate decay from execution degradation.** A Sharpe fall caused by widening slippage or a broker routing change looks identical here. Rule that out with `transaction-cost-analysis-tca-integration` and `backtest-vs-live-performance-divergence-tracking` before concluding the *signal* decayed.
- **On a single window as proof of anything.** One 60-observation test at a 2.5% level, run monthly across a book of strategies, generates false decommission signals by construction. Track the sequence of diagnoses, and correct for multiple testing across the book.

## Prerequisites

- Strategy per-period simple returns (`strategy_returns`) — not log returns, net of fees and costs.
- Peer benchmark index returns (`peer_returns`) on the **same** frequency, indexed on the same periods. The engine aligns on the shared index and reports how many observations were dropped; it does not forward-fill.
- Both series free of NaN/Inf across the aligned window, sorted ascending, with unique index labels. The engine raises on each of these rather than repairing them.
- At least `rolling_window_days` aligned observations (default 60). The diagnosis runs on the trailing window only.
- `periods_per_year` matching the return frequency (252 for daily bars).
- Thresholds you are willing to defend: the health threshold (default annualized Sharpe 0.50) is a **house default, not an external standard** — see `references/standards.md`.

## Workflow

1. **Align the two return series**:
   - Intersect the two indices. Observations present in only one series are dropped and counted in `warnings`.
   - **Decision point — an interior NaN is rejected, not dropped.** Dropping a gap inside the shared window makes non-adjacent periods adjacent and silently shrinks the sample, so the reported diagnosis comes from data the caller never sees. The engine raises `DecayDiagnosticError`. Duplicate index labels (which would turn the join into a partial cartesian product) and an unsorted index (which makes "the trailing window" the wrong observations) are rejected for the same reason.

2. **Compute annualized Sharpe ratios over the trailing window**:
   - $\text{Sharpe} = \dfrac{\overline{r - r_f}}{\text{sd}(r - r_f)}\sqrt{F}$ on the last `rolling_window_days` observations, with $r_f$ deducted per period.
   - **Decision point — a constant return series has an undefined Sharpe ratio, not a zero one.** Reporting 0.0 puts a zero-volatility, strictly profitable strategy below the 0.50 health threshold and classifies it as impaired. The engine returns `INCONCLUSIVE` with `target_sharpe = NaN` and an explanatory warning instead.

3. **Test the Sharpe difference for significance**:
   - $z = \dfrac{\hat{Sh}_t - \hat{Sh}_p}{\sqrt{\hat\theta}}$, with $\hat\theta = \dfrac{1}{T}\left[2 - 2\rho + \tfrac{1}{2}\left(\hat{Sh}_t^2 + \hat{Sh}_p^2 - 2\hat{Sh}_t\hat{Sh}_p\rho^2\right)\right]$ on per-period Sharpe ratios, where $\rho$ is the correlation between the two return series (Jobson and Korkie 1981, corrected by Memmel 2003).
   - **Decision point — $\rho$ is not optional.** A strategy and its own peer index are usually strongly correlated, and $\rho$ is the dominant term in $\hat\theta$. Ignoring it does not merely lose precision, it makes the statistic something with no known null distribution, and the $-1.96$ critical value then means nothing.
   - **Decision point — do not build the standard error from a history of overlapping rolling Sharpe ratios.** Consecutive 60-day windows share 59 of 60 observations, so their dispersion is an autocorrelation artifact, not a sampling error. Measured on a true null, that construction rejected at 5.1–6.6% against a nominal 2.5%.
   - $z$ is `None` — never 0.0 — when $\hat\theta$ collapses because the two series are effectively identical. `None` means "no test was performed"; 0.0 means "the target matched its peers exactly". A report that conflates them lets an untested strategy read as a healthy one.

4. **Classify on significance *and* absolute health**:
   - $z \le$ `idiosyncratic_z_threshold` **and** $\text{Sharpe}_{\text{peer}} \ge$ threshold **and** $\text{Sharpe}_{\text{target}} <$ threshold $\implies$ `IDIOSYNCRATIC_ALPHA_DECAY` → `DECOMMISSION_OR_RECODE`.
   - **Decision point — significance alone must not trigger decommissioning.** A strategy at an annualized Sharpe of 3.7 whose peers ran at 11.6 is significantly behind ($z = -10.95$) and is not decayed. Retiring it destroys working capacity to chase a hotter cohort. The engine classifies it `HEALTHY` and records the relative gap as a capital-allocation warning.
   - $\text{Sharpe}_{\text{target}} <$ threshold **and** $\text{Sharpe}_{\text{peer}} <$ threshold $\implies$ `MARKET_WIDE_REGIME_SHIFT` → `PAUSE_OR_REDUCE_RISK`. Joint impairment takes precedence: when peers are down too, the evidence does not support an idiosyncratic verdict.
   - $\text{Sharpe}_{\text{target}} \ge$ threshold $\implies$ `HEALTHY`.
   - Impaired target, healthy peers, but *not* significant $\implies$ `INCONCLUSIVE` → `MONITOR_CLOSELY`. This is the honest answer to a short, noisy sample, and it is the most common one.

5. **Read `warnings` before acting on the classification.** Alignment drops, small samples, undefined Sharpe ratios, and healthy-but-lagging verdicts all surface there. An empty list means no caveats applied.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating an ad-hoc z-score as a hypothesis test**: dividing a Sharpe difference by the standard deviation of *any* convenient historical series produces a number that looks like a z-score and has no null distribution. Only a statistic with a derived standard error justifies the $-1.96$ threshold, and only under its own assumptions.
- **Reporting an uncomputed statistic as 0.0**: a fallback that substitutes 0.0 when the test cannot be run guarantees the idiosyncratic branch never fires, because $0.0 > -1.96$. The strategy that most needs flagging is the one with the least history.
- **Decommissioning on relative underperformance**: peers outrunning a strategy is an allocation signal. Alpha decay means the strategy's *own* risk-adjusted return has fallen below what the mandate requires.
- **Confusing idiosyncratic decay with a regime shift**: retiring a robust strategy during a market-wide chop, when the peer group is equally impaired, permanently forfeits the recovery.
- **Evaluating performance in isolation**: reading a drawdown without a peer benchmark cannot distinguish the two causes at all — it is the failure this skill exists to prevent.
- **Quoting "95% confidence" for a one-sided threshold**: $-1.96$ is the 2.5th percentile of the standard normal. Used one-sided it is a 2.5% false-positive rate, not 5%, and the confidence attaches to the test's assumptions, not to the trading conclusion.
- **Annualizing autocorrelated returns by $\sqrt{252}$**: Lo (2002) shows the $\sqrt{F}$ rule is invalid under serial correlation and inflates the Sharpe ratio of a positively autocorrelated series. Both the reported Sharpe ratios and the health threshold comparison are affected.
- **Silently dropping NaNs to keep the diagnosis running**: half the sample can disappear inside a `dropna()` and still produce a confident-looking `HEALTHY` verdict from 60 of 120 observations.
- **Re-running the test until it fires**: a monthly diagnosis on a 40-strategy book is 480 tests a year. At a 2.5% level that is a dozen false decommission signals annually before any strategy actually decays.
- **Changing the peer index mid-monitoring**: the classification is defined relative to the benchmark. Swapping it resets the comparison; record the peer index identity with every report.

## Verification

- Feed 60 observations alternating $0.02 / 0.00$ against the same pattern rolled by one, at a zero risk-free rate. Both annualized Sharpe ratios must equal $\sqrt{252 \times 59/60} = 15.7417$; the difference and $z$ must be exactly 0.0 and the one-sided p-value 0.5, **despite** the two series being perfectly negatively correlated.
- Cross-check $z$ against an independent delta-method derivation, $\nabla f' \Omega \nabla f$, using the covariance matrix $\Omega$ of Jobson-Korkie/Memmel. The two must agree to 9 decimal places.
- Simulate 2000 true nulls (equal Sharpe ratios, $\rho = 0.7$, 260 observations, window 60) and confirm the empirical one-sided rejection rate sits near the nominal 2.5% — it measures 2.65%. The pre-fix statistic measured 5.05% on the identical fixture.
- Supply exactly `rolling_window_days` observations for a strategy at an annualized Sharpe of $-8.33$ against peers at $+1.67$. Confirm `IDIOSYNCRATIC_ALPHA_DECAY` with $z = -3.21$, not `INCONCLUSIVE` with $z = 0.0$.
- Set target and peer to the same shock series scaled to annualized Sharpe ratios of 3.74 and 11.57. Confirm $z = -10.95$, classification `HEALTHY`, no `DECOMMISSION` in the recommended action, and a capital-allocation entry in `warnings`.
- Confirm a constant return series yields `INCONCLUSIVE`, `relative_sharpe_z_score is None`, and `target_sharpe` NaN — not a Sharpe of 0.0 classified as impaired.
- Confirm $z$ is unchanged between `periods_per_year` 252 and 12 at a zero risk-free rate, while the reported Sharpe ratios differ by $\sqrt{252/12}$.
- Negative checks: an interior NaN, an Inf, a return at or below $-100\%$, duplicate index labels, an unsorted index, disjoint indices, fewer than `rolling_window_days` aligned observations, 2D input, non-numeric input, a non-finite risk-free rate, and each out-of-range constructor argument must all raise `DecayDiagnosticError`.
- Run `python -m unittest discover -s skills/strategy-performance-decay-detection-vs-market-wide-decay/scripts` and confirm 100% pass rate.

## Related Skills

- `strategy-lifecycle-retirement-criteria`
- `strategy-underperformance-remediation-decision-tree`
- `strategy-decommissioning-and-position-unwind-procedure`
- `benchmark-relative-performance-attribution`
- `benchmark-selection-for-strategy-evaluation`
- `backtest-vs-live-performance-divergence-tracking`
