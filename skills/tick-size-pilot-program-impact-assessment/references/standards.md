# Institutional Market Microstructure & Tick Size Standards

## 1. SEC Tick Size Pilot Program Test Group Structure (Historical Reference Framework)
| Group | Tick Increment | Quoting Constraints | Trading Constraints | Special Rules |
| :--- | :--- | :--- | :--- | :--- |
| **Control Group** | $0.01 | Standard $0.01 | Standard $0.01 | Existing NMS rules apply |
| **Test Group 1 (TG1)** | $0.05 | Mandatory $0.05 quotes | Unconstrained ($0.01 execution allowed) | Quote widening evaluation |
| **Test Group 2 (TG2)** | $0.05 | Mandatory $0.05 quotes | Mandatory $0.05 trades | Trade widening evaluation |
| **Test Group 3 (TG3)** | $0.05 | Mandatory $0.05 quotes | Mandatory $0.05 trades | **Trade-At Rule** (Prevents price matching on off-exchange venues without price improvement) |

## 2. Microstructure Spread Metric Definitions & Formulas
1. **Quoted Spread ($)**:
   $$\text{Quoted Spread} = P_{\text{ask}} - P_{\text{bid}}$$
2. **Effective Spread ($)**:
   $$\text{Effective Spread} = 2 \times D \times (P_{\text{trade}} - P_{\text{mid}})$$
   *(where $D = +1$ for buy aggressor, $-1$ for sell aggressor)*
3. **Realized Spread (5-Minute, $)**:
   $$\text{Realized Spread}_{5m} = 2 \times D \times (P_{\text{trade}} - P_{\text{mid}, t+5m})$$
4. **Adverse Selection (bps)**:
   $$\text{Adverse Selection} = \frac{\text{Effective Spread} - \text{Realized Spread}_{5m}}{P_{\text{mid}}} \times 10,000$$

## 3. Algorithm Recalibration Matrix by Tick Regime
| Execution Strategy | Widened Tick Regime ($0.05) Impact | Recommended Parameter Tuning |
| :--- | :--- | :--- |
| **Passive Market Making** | Spread widens; L1 queue depth increases by 300–500%; fill latency increases. | Enforce Pegged Primary orders with offset; reduce max inventory holding time; increase cancel frequency. |
| **TWAP / VWAP Slicing** | Crossing the spread becomes 5x more expensive ($0.05 vs $0.01). | Increase passive limit slicing allocation to 80%+; enforce strict price caps. |
| **Momentum Taker** | Higher slippage on market orders. | Raise signal conviction threshold before triggering IOC / Market sweep orders. |