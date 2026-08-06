# Institutional Total Return Swap (TRS) Derivatives Standards

## 1. Mathematical Formulas for TRS Cash Flow Legs
1. **Total Return Leg ($)**:
   $$\text{Capital Return} = N_{\text{shares}} \times (P_{\text{end}} - P_{\text{start}})$$
   $$\text{Manufactured Dividend} = N_{\text{shares}} \times \sum D_{\text{gross}} \times (1 - T_{\text{withholding}})$$
   $$\text{Total Return Leg} = \text{Capital Return} + \text{Manufactured Dividend}$$

2. **Funding Leg ($)**:
   $$\text{Funding Rate} = \text{Benchmark Rate} + \frac{\text{Spread Bps}}{10,000}$$
   $$\text{Funding Interest} = (N_{\text{shares}} \times P_{\text{start}}) \times \text{Funding Rate} \times \text{DayFraction}$$

3. **Net Cash Flow Reset ($)**:
   - **Total Return Receiver (Long Synthetic)**:
     $$\text{Net Settlement} = \text{Total Return Leg} - \text{Funding Interest}$$
   - **Total Return Payer (Short Synthetic)**:
     $$\text{Net Settlement} = \text{Funding Interest} - \text{Total Return Leg}$$

## 2. Benchmark Rate Day-Count Market Conventions
| Currency | Benchmark Rate | Standard Day-Count | Reset Frequency | Typical Prime Broker Spread |
| :--- | :--- | :--- | :--- | :--- |
| **USD** | SOFR / Fed Funds | `ACT/360` | Monthly / Quarterly | 35 – 75 bps |
| **EUR** | €STR / EURIBOR | `ACT/360` | Monthly / Quarterly | 40 – 80 bps |
| **GBP** | SONIA | `ACT/365` | Monthly / Quarterly | 35 – 75 bps |
| **JPY** | TONA | `ACT/365` | Monthly / Quarterly | 45 – 90 bps |

## 3. ISDA & Regulatory Margin Rules (Uncleared Margin Rules - UMR)
- **Initial Margin (IM)**: Required upfront collateral posted by both counterparties (typically 15% to 25% of gross notional for equity TRS).
- **Variation Margin (VM)**: Daily Mark-to-Market (MtM) cash margin call to cover intraday price movements.
- **Section 871(m) US Tax Withholding**: Applies 15–30% tax withholding on dividend-equivalent payments for swaps referencing US equities.