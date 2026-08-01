# Workflows for Reproducible ML Training Pipelines

1. **Global Seed Binding**:
   - Set global seeds for python random, numpy, and framework RNGs.
2. **Dataset & Spec Hashing**:
   - Compute SHA-256 hashes of training dataset and hyperparameter dict.
3. **Deterministic Execution**:
   - Run model training under deterministic execution settings.
4. **Manifest Signing**:
   - Generate and sign reproducibility manifest containing dataset, hyperparameter, and weights hashes.