# Institutional Warrants & Structured Product Standards

## 1. Warrant Category & Settlement Classification Matrix
| Warrant Type | Issuer Category | Entitlement Ratio ($R_{\text{ent}}$) | Barrier / Knock-Out Condition | Equity Dilution Risk |
| :--- | :--- | :--- | :--- | :--- |
| **Covered Call Warrant** | Investment Bank | $R_{\text{ent}} = \frac{1}{\text{Conversion Ratio}}$ | None | Zero (Cash / Hedged) |
| **Covered Put Warrant** | Investment Bank | $R_{\text{ent}} = \frac{1}{\text{Conversion Ratio}}$ | None | Zero (Cash / Hedged) |
| **Turbo Bull (CBBC)** | Investment Bank | $R_{\text{ent}} = \frac{1}{\text{Conversion Ratio}}$ | $S \le B_{\text{knockout}}$ | Zero (Knock-out Termination) |
| **Turbo Bear (CBBC)** | Investment Bank | $R_{\text{ent}} = \frac{1}{\text{Conversion Ratio}}$ | $S \ge B_{\text{knockout}}$ | Zero (Knock-out Termination) |
| **Equity Warrant** | Corporate Issuer | $R_{\text{ent}} = 1.0$ | None | **Dilutive** (New Shares Issued) |

---

## 2. Quantitative Valuation & Gearing Equations

### A. Warrant Black-Scholes Fair Value ($P_{\text{warrant}}$):
$$P_{\text{warrant, call}} = R_{\text{ent}} \times \left[ S N(d_1) - K e^{-r T} N(d_2) \right]$$

$$P_{\text{warrant, put}} = R_{\text{ent}} \times \left[ K e^{-r T} N(-d_2) - S N(-d_1) \right]$$

### B. Warrant Delta ($\Delta_{\text{warrant}}$):
$$\Delta_{\text{warrant, call}} = R_{\text{ent}} \times N(d_1)$$

$$\Delta_{\text{warrant, put}} = R_{\text{ent}} \times \left[ N(d_1) - 1 \right]$$

### C. Simple Gearing vs Effective Gearing:
$$\text{Simple Gearing} = \frac{S \times R_{\text{ent}}}{P_{\text{warrant}}}$$

$$\text{Effective Gearing} = \text{Simple Gearing} \times |\Delta_{\text{raw}}| = \left( \frac{S \times R_{\text{ent}}}{P_{\text{warrant}}} \right) \times N(d_1)$$

---

## 3. Delta-Neutral Hedging & Rebalancing Equation
For a market maker or quant desk holding a position of $N_{\text{warrants}}$ covered warrants:

$$\text{Required Underlying Shares} = N_{\text{warrants}} \times \Delta_{\text{warrant}}$$

$$\text{Net Rebalance Shares} = \text{Required Underlying Shares} - \text{Current Hedged Shares}$$

### Knock-Out Discontinuity Rule:
When $S \le B_{\text{knockout}}$ (Bull CBBC), $\Delta_{\text{warrant}}$ drops to $0.0$ immediately. The hedging engine must liquidate $100\%$ of underlying hedged shares instantly.