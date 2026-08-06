# Institutional US IRS Wash Sale Operations Checklist

## Trade Ingestion & Tax Lot Matching
- [ ] **Chronological Execution Logging**: Ensure trade executions (Date, Symbol, Side, Price, Quantity) are sorted in chronological order.
- [ ] **FIFO Tax Lot Allocation**: Confirm sell trades are matched against open buy tax lots on a strict First-In, First-Out (FIFO) basis.
- [ ] **Loss Realization Flagging**: Identify all sell executions resulting in a realized capital loss ($P_{\text{sell}} < P_{\text{basis}}$).

## 61-Day Window & Cost Basis Adjustments
- [ ] **61-Day Window Scanning**: Scan buy trades occurring within $[t_{\text{loss}} - 30\ \text{days},\; t_{\text{loss}} + 30\ \text{days}]$.
- [ ] **Loss Disallowance Calculation**: Calculate disallowed loss amount for matched replacement shares.
- [ ] **Replacement Share Basis Addition**: Add disallowed loss per share to the cost basis of replacement shares ($\text{Price}_{\text{buy}} + \text{Loss/Share}$).

## Form 1099-B & Year-End Tax Reporting
- [ ] **Form 1099-B Box 1g Population**: Aggregate total disallowed wash sale losses for Box 1g disclosure.
- [ ] **Net Allowed Taxable PnL Verification**: Confirm $\text{Net Taxable PnL} = \text{Gross Realized PnL} + \text{Disallowed Loss}$.
- [ ] **Audit Trail Storage**: Retain tax lot match records and 61-day window scan logs for 7 years.