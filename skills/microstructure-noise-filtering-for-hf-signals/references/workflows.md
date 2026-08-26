# Workflows — microstructure-noise-filtering-for-hf-signals

## 1. Validate the quote stream

Run before any filtering. These estimators are sequential: a bad tick does not fail
locally, it corrupts every estimate after it.

| Check | Why it is fatal, not cosmetic |
|---|---|
| All fields finite | A single `NaN` propagates through every subsequent Kalman/EMA state. Worse, `nan > 0` is `False`, so the dispersion audit reports a plausible `0.00%` reduction instead of an error. |
| Prices strictly positive | A non-positive quote is a feed artefact, not a market. |
| $P_{\text{bid}} \leq P_{\text{ask}}$ | A crossed book's midpoint has no economic meaning, and the weighted mid inverts its sign. A *locked* book ($P_{\text{bid}} = P_{\text{ask}}$) is legitimate and is accepted. |
| Volumes $\geq 0$ on **each side** | A per-side check, not a check on the sum. $V_{\text{bid}} = 10$, $V_{\text{ask}} = -5$ passes `total > 0` and drives the weighted mid to $100.40$ on a $100.00/100.20$ book. |
| Timestamps non-decreasing | Duplicates are normal in real feeds; an *inverted* timestamp means the stream is unsorted and every sequential estimate is wrong. |

Outlier screening is a separate, earlier stage — these are linear filters and do not
reject fat-finger prints. See `backtest-outlier-and-bad-tick-filtering`.

## 2. Compute the midpoint

$$S_{\text{mid}} = \frac{P_{\text{bid}} + P_{\text{ask}}}{2}$$

Note what this step already accomplishes: taking the midpoint removes bid-ask bounce
(Roll 1984), which is a property of *trade* prices alternating between the bid and the
ask. Nothing downstream of here is removing bounce, because there is none left to
remove. `RawTick.last_price` — the series that does bounce — is carried but not
consumed.

## 3. Select and apply the estimator

### `KALMAN` — local level model

$$y_t = \mu_t + \varepsilon_t,\ \varepsilon_t \sim N(0, R) \qquad \mu_t = \mu_{t-1} + \eta_t,\ \eta_t \sim N(0, Q)$$

Per tick:

```
p_predict = p_est + Q                    # time update
K         = p_predict / (p_predict + R)  # gain
x_est     = x_est + K * (mid - x_est)    # measurement update
p_est     = (1 - K) * p_predict
```

Initialised with $x_0 = S_{\text{mid},0}$ and $P_0 = 1.0$. That prior is deliberately
diffuse: $K_1 \approx 0.99$, so the filter adopts the first observation, then the
covariance collapses within one update ($K_2 \approx 0.50$ at the defaults) and
converges to $K^*$.

### `WEIGHTED_MID` (alias `MICRO_PRICE`) — imbalance-weighted mid

$$w = \frac{V_{\text{bid}}}{V_{\text{bid}} + V_{\text{ask}}}, \qquad W = w P_{\text{ask}} + (1-w) P_{\text{bid}} = \frac{V_{\text{ask}} P_{\text{bid}} + V_{\text{bid}} P_{\text{ask}}}{V_{\text{bid}} + V_{\text{ask}}}$$

Heavy bid depth pulls $W$ toward the ask. With volumes validated non-negative, $W$ is
guaranteed to lie within $[P_{\text{bid}}, P_{\text{ask}}]$. When both queues are empty
the imbalance is undefined and the estimator falls back to the midpoint.

This is the **weighted mid-price**, not Stoikov's micro-price — see
`references/standards.md`. It is a fair-value estimator, not a smoother.

### `EMA` — exponential smoothing

$$\tilde{P}_t = \alpha P_t + (1-\alpha)\tilde{P}_{t-1}, \qquad \alpha = \frac{2}{N+1}$$

Seeded from the first midpoint. $N \geq 1$ is enforced: $N = 1$ gives $\alpha = 1$
(pass-through), $N = 0$ gives $\alpha = 2$ — an amplifying oscillator that diverges from
the data instead of smoothing it.

## 4. Tune $Q$ and $R$ through the steady-state gain

Do not tune by eye. Only the ratio $q = Q/R$ affects the steady state; scaling both
together changes nothing.

At the fixed point $P^- = P + Q$, $P = (1-K)P^-$, $K = P^-/(P^-+R)$ collapse to

$$RK^2 + QK - Q = 0 \quad \Longrightarrow \quad K^* = \tfrac{1}{2}\left(\sqrt{q^2 + 4q} - q\right)$$

In steady state the filter *is* an EMA with $\alpha = K^*$, so
$N_{\text{eff}} = 2/K^* - 1$ expresses a $(Q, R)$ pair as "roughly an $N$-tick average".

| $q = Q/R$ | $K^*$ | Effective span | Character |
|---|---|---|---|
| $10^{-3}$ (defaults) | $0.0311$ | $\approx 63$ ticks | Heavy smoothing, noticeable lag |
| $0.1$ | $0.2702$ | $\approx 6.4$ ticks | Moderate |
| $1.0$ | $0.6180$ | $\approx 2.2$ ticks | Light — mostly tracks the midpoint |

Use `steady_state_kalman_gain(Q, R)` and `kalman_effective_span(Q, R)` to read these off
directly. Check the effective span against the horizon of the signal being built: a
63-tick average is not a useful input to a 5-tick-horizon prediction.

## 5. Audit the dispersion change

$$\eta_{\sigma} = \left(1 - \frac{\sigma_{\text{filtered}}}{\sigma_{\text{raw}}}\right) \times 100\%, \qquad \eta_{\sigma^2} = \left(1 - \frac{\sigma^2_{\text{filtered}}}{\sigma^2_{\text{raw}}}\right) \times 100\%$$

Both are reported: `noise_reduction_pct` and `noise_variance_reduction_pct`. They are
different numbers — a filter cutting $\sigma$ by 54% cuts variance by 79%. Quote each
under its own name.

Interpret `status` by mode:

| Mode | `NOISE_FILTERING_SUCCESS` | `NOISE_FILTERING_NO_REDUCTION` |
|---|---|---|
| `KALMAN` | Working as intended | Mistuned — $q$ too high, tracking noise |
| `EMA` | Working as intended | Span too short |
| `WEIGHTED_MID` | Unusual; the book was near-balanced throughout | **Expected.** Not a failure — the weighted mid swings with imbalance and is normally *more* dispersed than the midpoint |

For `WEIGHTED_MID`, evaluate predictive power against forward returns instead — see
`order-book-microstructure-signal-research`.

## 6. Generate the report

`MicrostructureFilterReport` carries the per-tick series, both reduction metrics, the
dispersion ratio, and — for `KALMAN` — `kalman_snr_q`, `kalman_steady_state_gain`, and
`kalman_effective_span`, so the parameterisation is auditable alongside its result.
