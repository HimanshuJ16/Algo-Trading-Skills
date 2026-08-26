# Pre-Flight Checklist — microstructure-noise-filtering-for-hf-signals

## Input data

- [ ] Ticks sorted by non-decreasing `timestamp_epoch` (duplicates fine, inversions are not)?
- [ ] Non-finite (`NaN`/`Inf`) prices and volumes rejected **before** filtering, not skipped over?
- [ ] Crossed quotes ($P_{\text{bid}} > P_{\text{ask}}$) repaired or dropped upstream?
- [ ] Volumes checked **per side** for negativity, not just as a positive total?
- [ ] Fat-finger prints screened out separately (linear filters attenuate but do not reject them)?

## Filter selection

- [ ] Filter chosen deliberately: `KALMAN`, `WEIGHTED_MID` (alias `MICRO_PRICE`), or `EMA`?
- [ ] If using `WEIGHTED_MID`: understood that it is the **weighted mid-price**, not Stoikov's micro-price, and that it is a fair-value estimator rather than a smoother?
- [ ] Confirmed that filtering the midpoint does **not** remove bid-ask bounce — the midpoint never carried it?

## Parameterisation

- [ ] $Q > 0$ and $R > 0$ set from the ratio $q = Q/R$, not tuned by eye?
- [ ] `kalman_effective_span(Q, R)` checked against the signal's horizon (defaults ≈ 63 ticks)?
- [ ] Lag from the chosen gain accepted against the strategy's latency budget?
- [ ] `ema_span_n` $\geq 1$?
- [ ] `price_precision` matches the instrument — 4 equities, **5 FX**, up to **8 crypto**?

## Audit

- [ ] `noise_reduction_pct` (standard deviation) and `noise_variance_reduction_pct` (variance) each quoted under the correct name?
- [ ] `status` interpreted per mode — `NOISE_FILTERING_NO_REDUCTION` is expected for `WEIGHTED_MID`, a mistuning signal for `KALMAN`/`EMA`?
- [ ] `signal_to_noise_ratio` understood as an amplitude dispersion ratio, distinct from `kalman_snr_q` ($Q/R$) and from a power SNR?
- [ ] For `WEIGHTED_MID`: evaluated on predictive power against forward returns rather than on dispersion reduction?
- [ ] Filter parameters logged alongside results so the run is reproducible?
