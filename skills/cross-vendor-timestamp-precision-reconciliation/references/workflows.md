# Workflows for Cross-Vendor Timestamp Precision Reconciliation

1. **Vendor Parsing**:
   - Parse numeric/string timestamp $T_{\text{raw}}$ based on `precision_format`.
2. **Nanosecond Epoch Normalization**:
   - Convert to 64-bit integer nanoseconds UTC ($t_{\text{ns}}$).
3. **Temporal Sorting & OOO Interception**:
   - Sort multi-vendor tick array by $t_{\text{ns}}$.
   - Flag out-of-order sequence arrivals ($\Delta t < 0$).
4. **Audit Reporting**:
   - Log precision tier distribution and clock drift warnings.
