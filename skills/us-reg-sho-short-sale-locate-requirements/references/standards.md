# Institutional SEC Regulation SHO Short Sale Standards

## 1. SEC Regulation SHO Rules Matrix (17 CFR § 242.200 - 204)
| Reg SHO Rule Code | Mandate Name | Statutory Purpose & Requirements | Pre-Trade Verification Gate |
| :--- | :--- | :--- | :--- |
| **Rule 200** | **Order Marking** | All orders must be marked `LONG`, `SHORT`, or `SHORT_EXEMPT` at order entry | `validate_order_intent()` |
| **Rule 203(b)(1)** | **Locate Requirement** | Must borrow or obtain reasonable grounds to borrow prior to short sale | `locate_id` & pool check |
| **Rule 201** | **Alternative Uptick Rule (SSR)** | Circuit breaker triggered on $\ge 10\%$ intraday price drop; short price $> \text{NBB}$ | `price > NBB` check |
| **Rule 204** | **Close-Out (FTD)** | Participants must resolve Failures-to-Deliver by T+3 (standard) or T+5 | Clearing FTD Register |

## 2. Mathematical Rule 201 Short Sale Restriction (SSR) Price Test Formulas
1. **Rule 201 Trigger Condition (Intraday Price Drop)**:
   $$\Delta P_{\text{intraday}} = \frac{P_{\text{current}} - P_{\text{prior\_close}}}{P_{\text{prior\_close}}} \le -0.10 \implies \text{Trigger SSR for remainder of day + next trading day}$$

2. **Rule 201 SSR Permissible Execution Price**:
   - **For `SHORT` Marked Orders**:
     $$P_{\text{short}} > \text{NBB} \implies \text{COMPLIANT}$$
     $$P_{\text{short}} \le \text{NBB} \implies \text{VIOLATION (REJECTED)}$$
   - **For `SHORT_EXEMPT` Marked Orders**:
     $$P_{\text{short\_exempt}} \le \text{NBB} \implies \text{COMPLIANT (Statutory Exception Applies)}$$

## 3. Mandatory Locate Pool Capacity Allocation Math
For a locate record $L_i$ with allocated quantity $Q_{\text{allocated}}$:
$$Q_{\text{remaining}} = Q_{\text{allocated}} - \sum_{k \in \text{Executed Orders}} Q_k$$
An order of size $Q_{\text{order}}$ requires:
$$Q_{\text{order}} \le Q_{\text{remaining}} \quad \text{AND} \quad T_{\text{current}} < T_{\text{expires}}$$

