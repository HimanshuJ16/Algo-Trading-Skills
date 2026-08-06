# Institutional Vendor Corporate Action Adjustment Standards

## 1. Vendor Adjustment Methodology Classification Matrix
| Vendor Code | Methodology Name | Cash Dividend Treatment | Stock Split / Reverse Split | Volume Adjustment Rule |
| :--- | :--- | :--- | :--- | :--- |
| **CRSP** | `CRSP_TOTAL_RETURN` | Proportional Factor ($1 - D/P$) | Inverse Split Ratio ($1 / S$) | Volume Multiplied ($V \times S$) |
| **Bloomberg** | `BLOOMBERG_PROPORTIONAL` | Proportional Factor ($1 - D/P$) | Inverse Split Ratio ($1 / S$) | Volume Multiplied ($V \times S$) |
| **Refinitiv (Price)**| `SPLIT_ONLY` | Unadjusted (Ignored) | Inverse Split Ratio ($1 / S$) | Volume Multiplied ($V \times S$) |
| **Raw Exchange** | `RAW_UNADJUSTED` | Unadjusted | Unadjusted | Unadjusted |

---

## 2. Corporate Action Cumulative Adjustment Factor Equations

### A. Cash Dividend Factor Formula:
Given a cash dividend $D$ paid on ex-date $t_{\text{ex}}$ with closing price $P_{\text{cum}}$ prior to ex-date:
$$f_{\text{div}} = 1 - \frac{D}{P_{\text{cum}}}$$

### B. Stock Split Factor Formula:
Given a stock split ratio $S$ (e.g., $S = 2.0$ for 2-for-1 split):
$$f_{\text{split}} = \frac{1}{S} = \frac{\text{Shares}_{\text{old}}}{\text{Shares}_{\text{new}}}$$

### C. Cumulative Price Adjustment Factor ($F_t$):
For any historical date $t < t_{\text{ex}}$:
$$F_t = \prod_{i \ge t} f_i$$

$$P_{\text{adj}, t} = P_{\text{raw}, t} \times F_t$$

$$V_{\text{adj}, t} = V_{\text{raw}, t} \times \left( \frac{1}{F_t} \right)$$

---

## 3. Cross-Vendor Reconciliation Tolerance Standards
When reconciling two vendor adjusted price series ($P_{\text{Vendor A}, t}$ vs $P_{\text{Vendor B}, t}$):

$$\text{Percentage Difference}_t = \left| \frac{P_{\text{Vendor A}, t} - P_{\text{Vendor B}, t}}{\frac{P_{\text{Vendor A}, t} + P_{\text{Vendor B}, t}}{2}} \right| \times 100$$

- **Acceptable Alignment**: $\text{Percentage Difference}_t \le 0.5\%$.
- **Divergence Anomaly Flagged**: $\text{Percentage Difference}_t > 0.5\%$.
- **Reconciliation Failure**: Any single date exceeding tolerance threshold triggers an audit flag.

