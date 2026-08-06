# Institutional SEC Regulation NMS Rule 611 Standards

## 1. SEC Rule 611 Statutory Exemptions Matrix
| Rule 611 Exemption Code | Exemption Name | Statutory Purpose & Conditions | FIX Tag Marking |
| :--- | :--- | :--- | :--- |
| **Rule 611(b)(1)** | **Self-Help Exemption** | Declared against a venue experiencing $> 1.0\ \text{sec}$ latency or outage | Logged in System Audit |
| **Rule 611(b)(5/6)** | **Intermarket Sweep Order (ISO)** | Router simultaneously sweeps all superior protected venue quotes | `FIX Tag 18=f` or `Tag 269` |
| **Rule 611(b)(7)** | **Benchmark / VWAP Orders** | Executions priced against an independent benchmark (e.g. VWAP/TWAP) | `FIX Tag 40=G` / `8106=VWAP` |
| **Rule 611(b)(8)** | **Flickering Quotes Exemption** | Trade executed within $1.0\ \text{second}$ of a protected quote update | System Timestamp Comparison |
| **Rule 611(b)(9)** | **Stopped Orders** | Guaranteed execution price agreed upon prior to market quote shift | Trade Type Tagging |

## 2. Mathematical Trade-Through Detection Formulas
1. **Protected NBBO Calculation**:
   $$\text{NBB} = \max_{v \in \text{Automated}} \{ P_{\text{bid}, v} \mid \text{SelfHelp}(v) = \text{False} \}$$
   $$\text{NBO} = \min_{v \in \text{Automated}} \{ P_{\text{offer}, v} \mid \text{SelfHelp}(v) = \text{False} \}$$

2. **Trade-Through Conditions**:
   - **Buy Execution Trade-Through**:
     $$P_{\text{exec}} > \text{NBO} \implies \text{Trade-Through Violation (if no exemption)}$$
   - **Sell Execution Trade-Through**:
     $$P_{\text{exec}} < \text{NBB} \implies \text{Trade-Through Violation (if no exemption)}$$

3. **Trade-Through Severity in Basis Points (Bps)**:
   $$\text{TradeThrough}_{\text{bps}} = \frac{|P_{\text{exec}} - P_{\text{protected\_mid}}|}{P_{\text{protected\_mid}}} \times 10,000$$

## 3. SEC / FINRA CAT Reporting Mandate
- **Execution Timestamp Precision**: Microsecond-level ($10^{-6}\ \text{sec}$) timestamp logging for all executions and SIP quote updates.
- **CAT Reporting**: Audit logs must retain ISO route flags, protected NBBO snapshot prices, and Self-Help declaration records for 6 years.

