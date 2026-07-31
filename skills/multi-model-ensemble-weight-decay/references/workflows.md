# Workflows for Multi-Model Ensemble Weight Decay

1. **Memory Decay Update**:
   - Update rolling decayed loss or IC using exponential factor $\lambda$.
2. **Softmax Weighting**:
   - Calculate raw softmax weights using temperature parameter $\beta$.
3. **Demotion & Normalization**:
   - Demote models below weight floor or negative IC, and normalize active weights.
4. **Audit Report Generation**:
   - Output structured ensemble weight report.
