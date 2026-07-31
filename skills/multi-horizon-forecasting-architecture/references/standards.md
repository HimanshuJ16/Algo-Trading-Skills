# Standards for Multi-Horizon Forecasting

| Metric | Engineering Standard |
|---|---|
| Horizon Decay | Weights MUST decay with horizon length ($w \propto 1 / \sqrt{\tau}$ or $w \propto \text{IC}$). |
| Weight Normalization | All horizon weights MUST normalize to sum to 1.0 ($\sum \bar{w}_k = 1.0$). |
| Conflict Detection | Short-vs-long directional conflicts MUST be flagged when $\text{sign}(\hat{y}_{\text{short}}) \neq \text{sign}(\hat{y}_{\text{long}})$. |
