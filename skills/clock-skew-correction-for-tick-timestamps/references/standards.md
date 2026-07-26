# Standards for Clock Skew Correction

| Metric | Engineering Standard |
|---|---|
| Monotonicity Guarantee | Corrected timestamps MUST satisfy $T_i > T_{i-1}$ for all consecutive events $i$. |
| Lower Bound Estimation | Regression MUST be performed on the 5th percentile or minimum delay values within time windows, never on mean or median. |
| Time Unit Precision | Calculations must use floating-point seconds with at least microsecond/nanosecond precision (`float64`). |
