# Institutional UK FCA RTS 6 & FG18/9 Regulatory Standards

## 1. MiFID II RTS 6 Regulatory Mandates Matrix
| RTS 6 Article | Control Domain | Mandatory Control Action | Regulatory Limit Standard |
| :--- | :--- | :--- | :--- |
| **Article 12** | **Emergency Kill Switch** | Sub-second halt & venue mass cancel | Unconditional, automated execution |
| **Article 13(1)** | **Price Collar Control** | Block order outside price band | Max 2.5% deviation from NBBO mid |
| **Article 13(2)** | **Max Order Notional & Size** | Block order exceeding max notional/qty | £500k notional / 10k shares per order |
| **Article 13(3)** | **Order-to-Trade Ratio (OTR)** | Block order if OTR exceeds limit | Max 100 quote updates per fill |
| **Article 13(4)** | **Credit & Counterparty Limit** | Block order exceeding credit line | 100% of pre-allocated clearing limit |
| **Article 14** | **System Capacity & Stress** | Throttle/halt order flow at high load | 80% Warning / 95% Automated Halt |
| **Article 17** | **Post-Trade Reconciliation** | Reconcile fills vs internal book | Real-time / EOD zero discrepancy |

## 2. Mathematical Pre-Trade Control Formulas
1. **Price Collar Deviation (%)**:
   $$\text{Deviation}_{\%} = \frac{|P_{\text{order}} - P_{\text{NBBO\_mid}}|}{P_{\text{NBBO\_mid}}} \times 100$$
   $$\text{Condition: } \text{Deviation}_{\%} \le \text{Max Price Collar Limit (2.5\%)}$$

2. **Order-to-Trade Ratio (OTR)**:
   $$\text{OTR} = \frac{N_{\text{total\_orders}}}{N_{\text{total\_trades}}}$$
   $$\text{Condition: } \text{OTR} \le \text{Max OTR Threshold (100.0)}$$

3. **System Capacity Utilization (%)**:
   $$\text{Capacity Utilization}_{\%} = \frac{\text{Current Msg Rate (msgs/sec)}}{\text{Max System Capacity (msgs/sec)}} \times 100$$
   $$\text{Condition: } \text{Capacity Utilization}_{\%} < 95.0\%$$

## 3. FCA FG18/9 Governance & Audit Trail Requirements
- **Self-Assessment**: Annual FCA RTS 6 self-assessment audit signed off by SMF24 (Chief Operations Officer) or SMF16 (Compliance Oversight).
- **Immutable Log Retention**: Maintain pre-trade check logs and Kill Switch execution events for at least 5 years.