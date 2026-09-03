---
name: microstructure-noise-filtering-for-hf-signals
description: >-
  Use when a high-frequency signal is built on the raw top-of-book midpoint and the
  midpoint is too noisy to trade, applying a local-level Kalman filter and an
  imbalance-weighted mid instead.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: market-microstructure-latency
  tags: real-time-architecture, microstructure-noise, kalman-filter, local-level-model, weighted-mid-price, high-frequency-signals, tick-filtering
  brokers_frameworks: "Python Standard Library (math, dataclasses); Durbin-Koopman Local Level Model"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when building high-frequency alpha signals, momentum indicators, or statistical arbitrage models on tick-by-tick top-of-book data, and the raw quote midpoint $S_{\text{mid}} = \frac{P_{\text{bid}} + P_{\text{ask}}}{2}$ is too noisy to trade directly. At tick frequency the midpoint carries **quote flicker** (queue additions and cancellations that move the touch without moving fair value), **price discretization** at the tick size, and **transient depth imbalance**. Reacting to each of those movements generates false signals and fee drag. This module applies a 1D local level Kalman filter, an imbalance-weighted mid-price, or EMA smoothing, and reports how much dispersion each actually removed.

## When NOT to Use

- **To remove bid-ask bounce.** Bid-ask bounce is the negative first-order autocovariance Roll (1984) identifies in *transaction* prices oscillating between the bid and the ask. It is absent from midpoint data by construction, and this module filters the midpoint only. `RawTick.last_price` is carried for downstream use and is deliberately never consumed here. A trade-price series needs bounce-aware handling this skill does not implement.
- **When you need Stoikov's micro-price.** The `WEIGHTED_MID` mode computes the *weighted mid-price*. Stoikov (2018) defines the micro-price as a different object — the martingale limit of expected future mid-prices under a Markov chain on (imbalance, spread) — and shows it is generally *less* noisy than the weighted mid. Fitting it requires calibration this module does not perform.
- **When the tick stream is strongly irregularly spaced and you need time-consistent smoothing.** $Q$ is applied once per tick, not per unit of elapsed time, so the effective smoothing window varies with message rate. During a burst the filter smooths over a much shorter wall-clock interval than during a quiet period.
- **As a bad-tick or outlier filter.** These are linear filters: a single fat-finger print is attenuated but still shifts the state. Screen outliers first — see `backtest-outlier-and-bad-tick-filtering`.
- **When latency budget matters more than smoothness.** Every estimator here is causal and therefore lags. There is no parameter setting that removes both noise and lag.

## Prerequisites

- Top-of-book quote stream (`timestamp_epoch`, `bid_price`, `ask_price`, `last_price`, `bid_volume`, `ask_volume`), sorted by non-decreasing timestamp, with crossed quotes already repaired or dropped.
- Filter selection: `KALMAN`, `WEIGHTED_MID` (alias `MICRO_PRICE`), or `EMA`.
- For `KALMAN`: process noise $Q > 0$ (latent price variance per tick) and observation noise $R > 0$ (microstructure noise variance). Neither is estimated from the data — you supply both.
- For `EMA`: span $N \geq 1$.
- `price_precision` matching the instrument: 4 is equity default, FX needs 5, crypto often 8.

## Workflow

1. **Validate the tick stream before filtering.** These filters are sequential and stateful, so bad input does not fail locally — it corrupts every subsequent estimate. The engine rejects non-finite values, non-positive prices, crossed books, negative volumes, and out-of-order timestamps.
   - **Decision point — a `NaN` bid is not a missing value to skip past.** It poisons the Kalman/EMA state for the rest of the stream, and because `nan > 0` is `False` the resulting report shows a plausible `0.00%` reduction rather than an error. Reject it at the boundary.
   - **Decision point — duplicate timestamps are fine, inverted ones are not.** Same-microsecond ticks are normal in real feeds; a timestamp that goes *backwards* means the stream is unsorted and every sequential estimate downstream is wrong.

2. **Compute the midpoint**: $S_{\text{mid}} = \frac{P_{\text{bid}} + P_{\text{ask}}}{2}$.

3. **Apply the chosen estimator.**
   - **Kalman (local level model)** — $y_t = \mu_t + \varepsilon_t$, $\varepsilon_t \sim N(0, R)$; $\mu_t = \mu_{t-1} + \eta_t$, $\eta_t \sim N(0, Q)$, with gain $K_t = \frac{P_t^-}{P_t^- + R}$.
   - **Weighted mid-price** — $W = w P_{\text{ask}} + (1-w) P_{\text{bid}}$, $w = \frac{V_{\text{bid}}}{V_{\text{bid}} + V_{\text{ask}}}$, equivalently $\frac{V_{\text{ask}} P_{\text{bid}} + V_{\text{bid}} P_{\text{ask}}}{V_{\text{bid}} + V_{\text{ask}}}$. Heavy bid depth pulls the estimate toward the ask.
   - **EMA** — $\tilde{P}_t = \alpha P_t + (1-\alpha)\tilde{P}_{t-1}$, $\alpha = \frac{2}{N+1}$.
   - **Decision point — tune $Q$ and $R$ through the steady-state gain, not by trial and error.** Only the ratio $q = Q/R$ matters. The gain converges to $K^* = \tfrac{1}{2}\left(\sqrt{q^2 + 4q} - q\right)$, the positive root of $RK^2 + QK - Q = 0$, and in steady state the filter *is* an EMA with $\alpha = K^*$. Call `kalman_effective_span(Q, R)` to read a $(Q, R)$ pair as "roughly an $N$-tick average": the defaults $Q = 10^{-5}$, $R = 10^{-2}$ give $K^* \approx 0.0311$, an effective span of about 63 ticks.

4. **Audit the dispersion change.** Compare raw midpoint standard deviation $\sigma_{\text{raw}}$ against filtered $\sigma_{\text{filtered}}$:
   $$\eta_{\sigma} = \left(1 - \frac{\sigma_{\text{filtered}}}{\sigma_{\text{raw}}}\right) \times 100\%, \qquad \eta_{\sigma^2} = \left(1 - \frac{\sigma^2_{\text{filtered}}}{\sigma^2_{\text{raw}}}\right) \times 100\%$$
   `noise_reduction_pct` is the **standard deviation** reduction $\eta_{\sigma}$; `noise_variance_reduction_pct` is the **variance** reduction $\eta_{\sigma^2}$. They are different numbers for the same filter — do not quote one under the other's name.
   - **Decision point — `NOISE_FILTERING_NO_REDUCTION` means different things per mode.** For `WEIGHTED_MID` it is the *expected* outcome and not a failure. For `KALMAN` or `EMA` it means the filter is mistuned and is tracking noise rather than removing it.

5. **Generate the report**: `MicrostructureFilterReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Calling the weighted mid a noise filter.** It is a fair-value/bias correction, not a smoother. Because it swings with queue imbalance, its dispersion normally *exceeds* the midpoint's — measured at $-0.85\%$ "reduction" on the module's own smoke test. Judging it by dispersion reduction and concluding the filter is broken is the wrong reading; judge it by predictive power against forward returns instead (see `order-book-microstructure-signal-research`).
- **Claiming to have removed bid-ask bounce by filtering the midpoint.** The bounce was never in the midpoint. Taking $(P_{\text{bid}} + P_{\text{ask}})/2$ removes it before any filter runs; Roll's negative autocovariance lives in the trade-price series.
- **Reporting a standard-deviation reduction as a variance reduction.** They differ substantially: a filter that cuts $\sigma$ by $54\%$ cuts variance by $79\%$. The larger number is not the more impressive version of the same claim, it is a different quantity.
- **Letting a single `NaN` through.** It propagates silently through the entire remaining stream and the report still reads as a successful run.
- **Trusting a negative volume because the total is positive.** With $V_{\text{bid}} = 10$ and $V_{\text{ask}} = -5$, a naive `total > 0` guard passes and the weighted mid evaluates to $100.40$ on a $100.00/100.20$ book — a "price" outside the book. Validate each side, not the sum.
- **Setting $R$ too high to look smooth.** Large $R$ shrinks the gain and adds phase lag; the series looks clean in a chart and arrives too late to trade. Read the lag off `kalman_effective_span` before shipping.
- **Leaving `price_precision` at 4 for FX or crypto.** Four decimals quantizes EURUSD to the pip and destroys sub-pip structure; crypto needs up to 8.
- **Assuming constant $Q$ handles irregular tick spacing.** $Q$ is per-tick, not per-second, so the effective smoothing window contracts during bursts and expands when quiet.

## Verification

- **Kalman steady-state gain, derived independently of the code.** $K^*$ is the positive root of $RK^2 + QK - Q = 0$. At $q = Q/R = 1$ this reduces to $K^2 + K - 1 = 0$, so $K^* = \frac{\sqrt{5}-1}{2} = 0.6180339887$. Verify `steady_state_kalman_gain(1.0, 1.0)` returns it, and that iterating the Riccati recursion 200,000 times converges to the same value at $Q = 10^{-5}, R = 10^{-2}$ ($K^* = 0.0311267292$).
- **Weighted mid-price by hand.** Bid $100.00$ / Ask $100.20$ with $V_{\text{bid}} = 900$, $V_{\text{ask}} = 100$ gives $w = 0.9$ and $W = 0.9(100.20) + 0.1(100.00) = 100.18$ — shifted toward the ask. Verify `MICRO_PRICE` returns the same value as `WEIGHTED_MID`.
- **EMA by hand.** $N = 3 \Rightarrow \alpha = 0.5$; midpoints $[100, 110, 110]$ seeded from the first midpoint give exactly $[100.0, 105.0, 107.5]$.
- **Kalman noise reduction.** 1,000 ticks of a random walk ($\sigma_{\text{step}} = 0.003$) plus microstructure noise ($\sigma_{\text{noise}} = 0.05$), filtered at $Q = 10^{-5}, R = 10^{-2}$: expect roughly $54\%$ standard-deviation reduction ($79\%$ variance), status `NOISE_FILTERING_SUCCESS`.
- **Weighted mid regression.** The same stream under `WEIGHTED_MID` must report a *negative* `noise_reduction_pct`, `dispersion_reduced=False`, and status `NOISE_FILTERING_NO_REDUCTION` — not `NOISE_FILTERING_SUCCESS`.
- **Negative checks — each must raise `ValueError`:** an empty stream, an unsupported filter type, a `NaN`/`Inf` price or volume, a non-positive price, a crossed book ($P_{\text{bid}} > P_{\text{ask}}$), a negative volume, an out-of-order timestamp, $R \leq 0$, $Q < 0$, `ema_span_n` $\leq 0$, and a negative or non-integer `price_precision`. A *locked* book ($P_{\text{bid}} = P_{\text{ask}}$) and duplicate timestamps must both be accepted.
- Run `python -m unittest discover -s skills/microstructure-noise-filtering-for-hf-signals/scripts` and confirm 36 tests pass.

## Related Skills

- `order-book-microstructure-signal-research`
- `order-book-imbalance-signal-pipeline`
- `adverse-selection-measurement-for-passive-orders`
- `backtest-outlier-and-bad-tick-filtering`
