# Standards for Multi-Model Ensemble Weight Decay

| Metric | Engineering Standard |
|---|---|
| Memory Decay ($\lambda$) | Exponential decay factor MUST be between $0.80$ and $0.99$ (default $0.95$). |
| Weight Floor | Minimum weight floor MUST be enforced ($w_{\text{min}} \ge 0.02$). |
| Weight Normalization | Active ensemble weights MUST sum to $1.0$ ($\sum w_m = 1.0$). |
