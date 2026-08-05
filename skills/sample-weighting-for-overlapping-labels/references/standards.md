# Standards for Sample Weighting for Overlapping Labels

| Metric | Engineering Standard |
|---|---|
| Average Uniqueness Formula | $u_i = \frac{1}{\tau_i} \sum_{t=t_{i,0}}^{t_{i,1}} \frac{1}{c_t}$. |
| Weight Normalization | Total sample weights MUST sum to total sample count $N$ ($\sum w_i = N$). |
| Overlap Threshold | Datasets with average uniqueness $< 0.5$ MUST use sample weighting or purging. |
