# Pre-Flight Checklist

- [ ] Is Nested Cross-Validation (Outer CV / Inner Tuning CV) active?
- [ ] Are feature scalers fit strictly on inner training fold data?
- [ ] Is Purging applied to overlapping target label horizons?
- [ ] Is Embargoing buffer ($1.0\%$) applied following validation folds?
