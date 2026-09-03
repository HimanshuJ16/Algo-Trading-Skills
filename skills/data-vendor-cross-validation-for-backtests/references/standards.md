# Backtesting Methodology Standards — data-vendor-cross-validation-for-backtests

The tolerances below are this skill's **default configuration**, not thresholds mandated
by any exchange, regulator, or external standards body. No such published standard exists
for vendor-to-vendor bar reconciliation. Calibrate them per asset class and bar interval:
50 bps is loose for a liquid large-cap daily close and tight for an illiquid small-cap
1-minute bar.

| Validation Metric | Default Tolerance | Action on Breach | Fails Verdict |
|---|---|---|---|
| Per-Bar Close Price Delta | $\le 50$ bps | Flag bar as discrepant | Yes |
| Missing Bar Ratio | $\le 1.0\%$ of the union of timestamps | Fail cross-validation | Yes |
| Volume Spike Ratio | $\le 3.0\times$, symmetric $\max/\min$ | Flag for duplicate reporting audit | No |
| Bar Integrity (NaN/Inf close or volume, negative volume, zero reference close, duplicate timestamp) | None permitted | Report as integrity issue | Yes |

Volume flags are deliberately audit-only: a consolidated tape and a primary-exchange-only
feed legitimately disagree on volume for the same bar, so failing the run on volume alone
produces noise rather than signal. Price disagreement and unusable bars fail the run.

## Metric Definition

Close delta in basis points, with Vendor A as the reference vendor:

$$\Delta_{\text{close}} = \frac{|C_A - C_B|}{|C_A|} \times 10^4$$

The measure is asymmetric — swapping the vendors changes the denominator — so the primary
production vendor should always be passed as Vendor A. The absolute value in the
denominator keeps the metric well-defined for negative prices (negative futures
settlements are rare but real); a zero reference close is rejected as an integrity issue
rather than divided by.
