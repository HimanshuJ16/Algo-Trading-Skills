# Workflows for Cloud Cost Anomaly Detection

1. **Ingestion**:
   - Collect daily spend per service: $C_1, C_2, \dots, C_k$, tagged with `environment`.
   - Validate telemetry: costs may be negative (credits/refunds) but must be finite — NaN/inf values raise instead of silently classifying as NORMAL.
2. **Baseline Computation** (scoped to service + environment):
   - Compute mean $\mu = \frac{1}{N}\sum C_i$, population std dev $\sigma = \sqrt{\frac{1}{N}\sum (C_i - \mu)^2}$.
   - Flat baseline ($\sigma < 10^{-4}$, e.g. reserved capacity): the Z-score degenerates to the absolute dollar deviation $C_{\text{curr}} - \mu$.
   - No history for the (service, environment) pair: report baseline-UNKNOWN — do not present a missing baseline as a healthy NORMAL.
3. **Z-Score Audit**:
   - Calculate $Z = \frac{C_{\text{curr}} - \mu}{\sigma}$ and compare the UNROUNDED value against thresholds (a z of 1.996 rounds to 2.0 for display but is below the warning gate).
4. **Classification & Alerting**:
   - If $Z \ge 3.0$ AND percentage increase vs mean $> 30\%$ $\implies$ `CRITICAL` (dual gate: the pct condition prevents a $2 absolute deviation on a large flat baseline from escalating).
   - If $Z \ge 2.0$ $\implies$ `WARNING`.
   - Else $\implies$ `NORMAL`.
   - Zero-mean baseline with positive spend: percentage increase is unbounded ($\infty$) — the CRITICAL gate cannot be bypassed by a zero-cost baseline.
5. **Unit Cost Analysis**:
   - $\text{Unit Cost} = C_{\text{curr}} / \text{Trading\_Volume}$ (per trade; multiply by 10,000 for a per-10k-trades view). Pass the real volume — the default of 1.0 makes unit cost equal raw spend.
