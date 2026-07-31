# Workflows for Peg Order Types for Passive Execution

1. **Reference Price Determination**:
   - Resolve reference price (Best Bid, Best Ask, or Midpoint) based on peg type and side.
2. **Offset & Limit Cap Application**:
   - Add/subtract discretionary offset and clamp against protective limit price cap.
3. **Repositioning & Repricing Dispatch**:
   - Check if price shift exceeds minimum tick threshold before submitting modify request.
4. **Audit Report Generation**:
   - Output structured peg order report.