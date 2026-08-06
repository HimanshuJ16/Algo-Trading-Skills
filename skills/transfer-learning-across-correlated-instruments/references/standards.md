# Institutional Financial ML Transfer Learning Standards

## 1. Source-Target Asset Correlation & Selection Matrix
| Target Asset Class | Ideal Source Asset Class | Selection Criteria | Recommended Min Correlation ($r$) |
| :--- | :--- | :--- | :--- |
| **New Single Stock (IPO)** | Sector ETF (e.g. XLK, XLF) | Same GICS sector & market cap tier | $\ge 0.65$ |
| **New Altcoin Token** | BTC-USD / ETH-USD | Same L1/L2 ecosystem or DeFi cluster | $\ge 0.70$ |
| **Thinly Traded Corp Bond** | Liquid IG/HY Bond ETF (LQD/HYG) | Matching duration & credit rating | $\ge 0.75$ |
| **Exotic Forex Pair** | G10 Currency Benchmark Pair | Regional trade basket alignment | $\ge 0.60$ |

## 2. Transfer Learning Optimization & Regularization Formulas
1. **Source Pre-Training Loss Function**:
   $$\mathcal{L}_{\text{source}}(w) = \frac{1}{N_{\text{src}}} \sum_{i=1}^{N_{\text{src}}} (y_i^{\text{src}} - w^T X_i^{\text{src}})^2$$

2. **L2 Regularized Target Fine-Tuning Loss Function**:
   $$\mathcal{L}_{\text{target}}(w) = \frac{1}{N_{\text{tgt}}} \sum_{j=1}^{N_{\text{tgt}}} (y_j^{\text{tgt}} - w^T X_j^{\text{tgt}})^2 + \lambda \| w - w_{\text{source}} \|_2^2$$
   *(where $\lambda$ penalizes parameter divergence from pre-trained source weights $w_{\text{source}}$)*

3. **Covariate Shift / Normalized Domain Distance**:
   $$\Delta_{\text{domain}} = \frac{1}{D} \sum_{d=1}^{D} \frac{|\mu_{d,\text{src}} - \mu_{d,\text{tgt}}|}{\sigma_{d,\text{src}}}$$

## 3. Negative Transfer Prevention Rules
- **Correlation Rejection**: If $\text{Corr}(Y_{\text{src}}, Y_{\text{tgt}}) < 0.60$, reject weight transfer.
- **Covariate Shift Rejection**: If $\Delta_{\text{domain}} > 2.0$, features exhibit extreme domain shift; reject feature transfer.
- **Out-of-Sample R² Validation**: Approve transfer deployment ONLY if $R^2_{\text{transfer}} > R^2_{\text{direct\_target}}$.