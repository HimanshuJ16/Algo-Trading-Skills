# Institutional Weather Derivatives & Niche Instrument Standards

## 1. CME Weather Derivatives Specification Standard
| Contract Type | Underlying Index | Base Temp | Contract Multiplier | Settlement Type | Primary Use Case |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **CME HDD Futures ($VX$)** | Heating Degree Days | $65^\circ\text{F}$ | **\$20.00 per index point** | Cash Settled | Winter Energy Risk Hedging |
| **CME CDD Futures ($VY$)** | Cooling Degree Days | $65^\circ\text{F}$ | **\$20.00 per index point** | Cash Settled | Summer Power Peak Hedging |
| **CME CAT Futures ($VZ$)** | Cumulative Avg Temp | N/A ($^\circ\text{C}$) | **\$20.00 per index point** | Cash Settled | European Weather Hedging |
| **CME HDD Options** | HDD Index Call/Put | $65^\circ\text{F}$ | **\$20.00 per index point** | Cash Settled | Asymmetric Weather Tail Risk |
| **OTC Capped Weather Swap** | Custom Degree Days | $65^\circ\text{F}$ | Custom (\$10k-\$100k/pt) | Cash Settled | Utility Volume Revenue Protection |

---

## 2. Quantitative Valuation & Payoff Equations

### A. CME HDD / CDD Futures Settlement Payoff:
$$\text{Payoff}_{\text{futures}} = I_{\text{accumulated}} \times \$20.00$$

### B. CME Weather Option Payoff Equations:
$$\text{Payoff}_{\text{call}} = \max\left(0,\; I_{\text{accumulated}} - K\right) \times \$20.00$$

$$\text{Payoff}_{\text{put}} = \max\left(0,\; K - I_{\text{accumulated}}\right) \times \$20.00$$

### C. OTC Capped Weather Swap Payoff ($P_{\text{swap}}$):
$$P_{\text{uncapped}} = \left( I_{\text{accumulated}} - K \right) \times M_{\text{tick}}$$

$$P_{\text{swap}} = \text{sign}(P_{\text{uncapped}}) \times \min\left( |P_{\text{uncapped}}|,\; C_{\text{max\_payout}} \right)$$

### D. Burn Analysis Fair Value ($V_{\text{burn}}$):
$$V_{\text{burn}} = \frac{1}{N} \sum_{j=1}^{N} \text{Payoff}\left( I_{\text{historical, } j} \right)$$

Where $I_{\text{historical, } j}$ is the accumulated index for historical season $j$, and $N$ is the number of historical seasons (typically 20-30 years).