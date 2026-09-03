# Standards — microstructure-noise-filtering-for-hf-signals

## Configuration defaults (calibrate before use)

These are the library's defaults, **not** industry standards. No regulator, exchange, or
standards body publishes a mandatory noise-filter parameterisation or a required
noise-reduction level. The right values depend on the instrument's tick size, its
quote-update rate, and the horizon of the signal being built. Calibrate each and record
the rationale.

| Parameter | Default | What it actually does |
|---|---|---|
| `kalman_process_noise_q` ($Q$) | $10^{-5}$ | Latent efficient-price variance **per tick** (not per second). |
| `kalman_obs_noise_r` ($R$) | $10^{-2}$ | Observation (microstructure noise) variance. Supplied, never estimated from the data. |
| Implied $q = Q/R$ | $10^{-3}$ | The local level model's signal-to-noise ratio. Only this ratio affects the steady state — scaling $Q$ and $R$ together changes nothing. |
| Implied $K^*$ | $0.031127$ | Steady-state gain, $\tfrac{1}{2}(\sqrt{q^2+4q}-q)$. |
| Implied effective span | $\approx 63$ ticks | $2/K^* - 1$. The defaults smooth roughly like a 63-tick EMA. |
| `ema_span_n` ($N$) | $10$ | $\alpha = 2/(N+1)$. Must be $\geq 1$; $N = 0$ gives $\alpha = 2$, an amplifying oscillator. |
| `price_precision` | $4$ | Equity default. **FX needs 5, crypto often 8** — 4 quantizes EURUSD to the whole pip. |

## Estimator facts (verified against the cited sources)

| Fact | Source |
|---|---|
| Local level model (random walk plus noise): $y_t = \mu_t + \varepsilon_t$, $\mu_t = \mu_{t-1} + \eta_t$; the ratio $q = \sigma^2_\eta / \sigma^2_\varepsilon$ is the model's *signal-to-noise ratio* | Durbin, J. & Koopman, S.J., *Time Series Analysis by State Space Methods*, Ch. 2 |
| Steady-state gain $K^* = \tfrac{1}{2}\left(\sqrt{q^2+4q} - q\right)$, the positive root of $RK^2 + QK - Q = 0$ | Algebraic fixed point of the scalar Riccati recursion; unit-tested against 200,000 iterations of the recursion itself |
| Bid-ask bounce induces negative first-order autocovariance in **transaction** price changes: $\mathrm{Cov}(\Delta p_t, \Delta p_{t-1}) = -s^2/4$, giving $s = 2\sqrt{-\mathrm{Cov}}$. The effect is a property of trades alternating between bid and ask and is **absent from quote midpoints** | Roll, R. (1984), "A Simple Implicit Measure of the Effective Bid-Ask Spread in an Efficient Market", *Journal of Finance* 39(4), 1127–1139 |
| The **weighted mid-price** is $W = w P_{\text{ask}} + (1-w)P_{\text{bid}}$ with $w = V_{\text{bid}}/(V_{\text{bid}}+V_{\text{ask}})$. Stoikov's **micro-price** is a distinct estimator — the martingale limit of expected future mid-prices under a Markov chain on (imbalance, spread) — and is a martingale and generally *less noisy* than the weighted mid, which is neither | Stoikov, S. (2018), "The micro-price: a high-frequency estimator of future prices", *Quantitative Finance* 18(12), 1959–1966 |
| Under microstructure noise, realized variance computed at ever-higher sampling frequency converges to the *noise* variance rather than integrated volatility — the reason raw tick-frequency dispersion is not a volatility measurement | Zhang, L., Mykland, P.A. & Aït-Sahalia, Y. (2005), "A Tale of Two Time Scales", *Journal of the American Statistical Association* 100(472), 1394–1411 |

## Reported metrics — exact definitions

| Field | Definition | Not to be confused with |
|---|---|---|
| `noise_reduction_pct` | $(1 - \sigma_f/\sigma_r) \times 100$ | A **variance** reduction. A filter cutting $\sigma$ by 54% cuts variance by 79%. |
| `noise_variance_reduction_pct` | $(1 - \sigma_f^2/\sigma_r^2) \times 100$ | — |
| `signal_to_noise_ratio` | $\sigma_{\text{filtered}} / \sigma_{\text{residual}}$ | A **power** SNR (a variance ratio), and the local level model's $q = Q/R$ — which is reported separately as `kalman_snr_q`. This field is an amplitude dispersion ratio and nothing more. |
| `kalman_snr_q` | $Q/R$ | — (0.0 for non-Kalman modes) |
| `estimated_noise` | $S_{\text{mid}} - \hat{P}$ | For `KALMAN`/`EMA` this is the filter residual. For `WEIGHTED_MID` it is the **imbalance adjustment**, which is signal, not noise. |

## Engineering properties of this implementation

These are properties the code actually has, not external requirements.

- **$O(1)$ per tick, $O(n)$ memory.** Each filter is a scalar recursion; memory grows only because every `FilteredTick` is retained for the report.
- **Deterministic and stateless.** No RNG, no global state, no wall-clock reads; two calls with identical arguments return identical output.
- **Pure standard library.** `math`, `logging`, `dataclasses`, `typing` only — no NumPy or SciPy dependency.
- **Fail-fast validation.** Non-finite values, non-positive prices, crossed books, negative volumes, out-of-order timestamps, and invalid filter parameters raise `ValueError` rather than degrading silently.

## Known limitations

- **$Q$ is per-tick, not per-unit-time.** Irregular tick spacing means the effective
  smoothing window varies with message rate. Time-scaling $Q$ by the elapsed interval is
  not implemented.
- **$R$ is not estimated.** The caller supplies it. Estimating noise variance from the
  data requires the two-scales machinery of Zhang et al. (2005).
- **Causal filters lag.** No parameterisation removes both noise and lag.
- **Linear filters do not reject outliers.** A fat-finger print is attenuated, not
  removed, and still shifts the state.
- **Top-of-book only.** Depth beyond the touch is not used.
