# Workflows for LME Integration

1. **Metal Contract Spec Lookup**:
   - Retrieve lot size in metric tons ($25\text{ MT}$ for CA/AH vs $6\text{ MT}$ for NI).
2. **Prompt Date Validation**:
   - Validate benchmark `'3M'`, `'CASH'`, or ISO prompt date string.
3. **USD/MT Price Tick Audit**:
   - Verify price is a multiple of $\$0.50$/MT.
4. **Audit Report Generation**:
   - Output structured LME order report.
