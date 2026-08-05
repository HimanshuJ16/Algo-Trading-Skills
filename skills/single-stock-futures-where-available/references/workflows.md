# Workflows for Single Stock Futures Where Available

1. **Theoretical Fair Value Calculation**:
   - Model cost-of-carry: $F_{\text{fair}} = (S - \text{PV}(D)) \cdot e^{(r - q) T}$.
2. **Arbitrage Opportunity Screening**:
   - Compare market SSF price to fair value to generate Cash-and-Carry signals.
3. **Ex-Dividend Adjustment**:
   - Apply exchange corporate action adjustments on ex-dividend dates.
4. **Margin & Capital Optimization**:
   - Compare SSF initial margin vs spot stock Reg T margin requirements.