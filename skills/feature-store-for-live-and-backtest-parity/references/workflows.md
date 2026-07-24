# Deep Workflow Reference — feature-store-for-live-and-backtest-parity

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Implement Shared Feature Core (`ParityFeatureStoreEngine`):**
   - Implement feature calculations (`compute_features_from_window()`) as pure functions consumed by both batch and online runtimes.

2. **Execute Offline Batch Pipeline (`compute_batch_features`):**
   - Compute historical feature matrix over historical OHLCV bars.

3. **Execute Online Streaming Pipeline (`compute_online_feature`):**
   - Maintain rolling ring buffer ($N=\text{lookback}$). Update feature vector upon each incoming bar.

4. **Verify Bit-for-Bit Feature Parity (`validate_parity`):**
   - Stream identical bar series through both pipelines. Assert $|X_{\text{batch}} - X_{\text{online}}| \le 10^{-6}$.

## Failure Modes Observed in Production

- **Train-Test Skew:** Maintaining separate feature code for backtests and live trading, causing subtle calculation drift.
- **Inconsistent Rolling Warm-up Data:** Streaming live features without filling the rolling ring buffer with initial historical bars.

## Production Implementation Reference

- Reference code: `scripts/feature_store.py` (`ParityFeatureStoreEngine`, `FeatureVector`, `Bar`).
- Automated unit tests: `scripts/test_feature_store.py`.
