---
name: microstructure-noise-filtering-for-hf-signals
description: >-
  Quantitative microstructure noise filtering engine applying 1D Kalman Filtering, Volume-Weighted Micro-Price estimation, and Exponential Smoothing to eliminate bid-ask bounce in HFT tick streams.
domain: Market Microstructure Latency
subdomain: Signal Processing & Microstructure Noise Reduction
tags: ["microstructure-noise", "kalman-filter", "micro-price", "bid-ask-bounce", "high-frequency-signals", "ema-smoothing", "snr"]
brokers_frameworks: ["Kalman Filters", "NumPy / SciPy Signal", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when developing high-frequency alpha signals, momentum indicators, or statistical arbitrage models on tick-by-tick order book data. High-frequency price streams ($S_{\text{mid}} = \frac{P_{\text{bid}} + P_{\text{ask}}}{2}$) suffer from severe **Microstructure Noise** caused by bid-ask bounce, price discretization (tick size), and temporary depth imbalances. Chasing raw tick movements causes excessive false trading signals and fee drag. This module applies **1D Kalman Filtering**, **Volume-Weighted Micro-Price Estimation**, and **EMA Smoothing** to extract latent equilibrium prices with quantified Signal-to-Noise Ratio (SNR) improvements.

## Prerequisites

- High-frequency tick stream (`symbol`, `timestamp_epoch`, `bid`, `ask`, `last_price`, `bid_vol`, `ask_vol`).
- Filter configuration (`filter_type`: `'KALMAN'`, `'MICRO_PRICE'`, `'EMA'`, process noise $Q$, observation noise $R$, EMA span $N$).

## Workflow

1. **Raw Tick Preprocessing**:
   - Compute mid-price: $S_{\text{mid}} = \frac{P_{\text{bid}} + P_{\text{ask}}}{2}$.
2. **Noise Reduction Filtering**:
   - **Kalman Filter**: Update state estimate $\hat{x}_t$ using Kalman Gain $K_t = \frac{P_{t-1} + Q}{P_{t-1} + Q + R}$.
   - **Micro-Price**: Compute depth-weighted price $P_{\text{micro}} = \frac{V_{\text{ask}} P_{\text{bid}} + V_{\text{bid}} P_{\text{ask}}}{V_{\text{bid}} + V_{\text{ask}}}$.
   - **EMA**: Apply exponential smoothing $\tilde{P}_t = \alpha P_t + (1-\alpha)\tilde{P}_{t-1}$.
3. **Noise Variance & SNR Audit**:
   - Calculate raw price standard deviation ($\sigma_{\text{raw}}$) vs filtered standard deviation ($\sigma_{\text{filtered}}$).
   - Compute Noise Reduction Percentage: $\eta_{\text{reduction}} = \left(1 - \frac{\sigma_{\text{filtered}}}{\sigma_{\text{raw}}}\right) \times 100.0\%$.
4. **Audit Report Generation**: Output structured `MicrostructureFilterReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Over-Filtering Latency Lag**: Setting Kalman observation noise $R$ too high, creating excessive phase delay and missing fast market moves.
- **Ignoring Depth Imbalance**: Using simple mid-price instead of Volume-Weighted Micro-Price, ignoring heavy bid or ask queue weight.
- **Chasing Discrete Tick Bounce**: Trading on raw single-tick oscillations without noise filtering, incurring severe exchange fee losses.

## Verification

- Instantiate `MicrostructureNoiseFilterEngine`. Filter 1,000 noisy ticks ($S_0 = \$100.00$, $\sigma_{\text{noise}} = \$0.05$) using Kalman Filter ($Q=10^{-5}, R=10^{-2}$) $\implies$ verify smoothed output series, noise reduction percentage $> 30.0\%$, SNR improvement, and status `NOISE_FILTERING_SUCCESS`.
- Run `python scripts/test_microstructure_noise_filtering_for_hf_signals.py`.

## Related Skills

- `order-book-microstructure-signal-research`
- `adverse-selection-measurement-for-passive-orders`
---
