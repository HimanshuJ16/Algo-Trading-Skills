# Backtesting Methodology Standards — data-vendor-cross-validation-for-backtests

| Validation Metric | Tolerance | Action on Breach |
|---|---|---|
| Per-Bar Close Price Delta | $\le 50$ bps | Flag bar as discrepant |
| Missing Bar Ratio | $\le 1.0\%$ | Fail cross-validation |
| Volume Spike Ratio | $\le 3.0\times$ | Flag for duplicate reporting audit |

## Category

`backtesting-methodology` — see top-level `mappings/` directory.
