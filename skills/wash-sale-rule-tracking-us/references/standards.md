# Institutional US IRS Wash Sale Rule Standards (26 U.S.C. § 1091)

## 1. IRS § 1091 Statutory Rule Matrix
| Statutory Rule | IRS Regulation | Standard Interpretation | Disallowance Effect |
| :--- | :--- | :--- | :--- |
| **61-Day Window** | 26 U.S.C. § 1091(a) | $[t_{\text{loss}} - 30\ \text{days},\; t_{\text{loss}} + 30\ \text{days}]$ | Loss disallowed if replacement shares acquired in window |
| **Cost Basis Addition** | 26 U.S.C. § 1091(d) | Disallowed loss added to replacement share cost basis | Postpones tax deduction until replacement shares sold |
| **Holding Period Tacking** | 26 U.S.C. § 1223(3) | Loss lot holding period added to replacement lot | Preserves long-term vs short-term capital gain qualification |
| **Form 1099-B Box 1g** | IRS Form 1099-B | Report disallowed wash sale loss amount | Mandatory broker reporting to taxpayer and IRS |

---

## 2. Mathematical Cost Basis & Disallowance Equations

### A. Realized Loss Disallowance:
For a sell execution of $N_{\text{loss}}$ shares at price $P_{\text{sell}}$ with cost basis $P_{\text{basis}}$ ($P_{\text{sell}} < P_{\text{basis}}$):

$$\text{Loss Per Share} = P_{\text{basis}} - P_{\text{sell}}$$

$$\text{Total Realized Loss} = N_{\text{loss}} \times \text{Loss Per Share}$$

If $N_{\text{rep}}$ replacement shares are acquired within $[t_{\text{loss}} - 30,\; t_{\text{loss}} + 30]$:

$$\text{Matched Quantity} = \min(N_{\text{loss}}, N_{\text{rep}})$$

$$\text{Disallowed Loss} = \text{Matched Quantity} \times \text{Loss Per Share}$$

$$\text{Allowed Recognized Loss} = \text{Total Realized Loss} - \text{Disallowed Loss}$$

### B. Adjusted Cost Basis of Replacement Shares ($P_{\text{adj\_basis}}$):
$$P_{\text{adj\_basis}} = P_{\text{replacement\_buy}} + \left( \frac{\text{Disallowed Loss}}{\text{Matched Quantity}} \right) = P_{\text{replacement\_buy}} + \text{Loss Per Share}$$

---

## 3. IRS Form 1099-B Tax Summary Equation
- **Box 1d (Proceeds)**: $\sum P_{\text{sell}} \times N_{\text{sell}}$
- **Box 1e (Cost Basis)**: $\sum P_{\text{basis}} \times N_{\text{sell}}$
- **Box 1g (Wash Sale Loss Disallowed)**: $\sum \text{Disallowed Loss}$
- **Net Reportable Taxable Gain/Loss**: $\text{Proceeds} - \text{Cost Basis} + \text{Box 1g}$