# Institutional Market Abuse & Manipulation Surveillance Standards

## 1. Regulatory Authority & Rule Framework
| Jurisdiction | Regulatory Body | Statutory Rule / Article | Prohibited Activity | Primary Control |
| :--- | :--- | :--- | :--- | :--- |
| **United States** | CFTC | CFTC Rule 1.38 / 7 U.S.C. § 6c(a) | Wash Sales / Non-Bona Fide Trades | Self-Match Prevention (SMP) |
| **United States** | SEC / FINRA | SEC Rule 10b-5 / FINRA Rule 5210 | Spoofing & Layering | Order-to-Trade Ratio Limits |
| **United States** | CFTC | Dodd-Frank § 747 (7 U.S.C. § 6c(a)(5)(C)) | Bidding/Offering with Intent to Cancel | Microsecond Lifespan Tracking |
| **European Union** | ESMA | MiFID II RTS 6 Article 13 & MAR | Algorithmic Market Manipulation | Pre-Trade Kill Switches & Audits |

---

## 2. Quantitative Abuse Metric Formulas

### A. Order Cancellation Ratio ($R_{\text{cancel}}$):
$$R_{\text{cancel}} = \left( \frac{N_{\text{canceled}}}{N_{\text{placed}}} \right) \times 100$$

Where $N_{\text{canceled}}$ is total canceled orders and $N_{\text{placed}}$ is total submitted orders per trader. If $R_{\text{cancel}} \ge 90\%$, an alert is triggered.

### B. Average Order Lifespan ($\bar{T}_{\text{lifespan}}$):
$$\bar{T}_{\text{lifespan}} = \frac{1}{N_{\text{canceled}}} \sum_{i=1}^{N_{\text{canceled}}} \left( t_{\text{cancel}, i} - t_{\text{place}, i} \right) \times 1000\ \text{ms}$$

Suspicious threshold: $\bar{T}_{\text{lifespan}} < 1,000\ \text{ms}$ when accompanied by opposite-side fills.

---

## 3. Wash Trade & Spoofing Condition Rules

### Wash Trade Match Condition:
$$\text{Trader}_A == \text{Trader}_B \quad \land \quad \text{Side}_A \neq \text{Side}_B \quad \land \quad \text{Price}_A == \text{Price}_B \quad \land \quad |t_A - t_B| \le \Delta t_{\text{window}}$$

### Spoofing / Layering Condition:
$$\text{Action}_1 = \text{PLACE}(\text{Side}_X, \text{LargeQty}) \rightarrow \text{Action}_2 = \text{CANCEL}(\text{Side}_X) \ [ \Delta t < 1000\text{ms} ] \rightarrow \text{Action}_3 = \text{FILL}(\text{Side}_Y)$$

Where $\text{Side}_X \neq \text{Side}_Y$.