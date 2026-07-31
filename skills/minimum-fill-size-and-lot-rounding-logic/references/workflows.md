# Workflows for Minimum Fill Size & Lot Rounding

1. **Board Lot Rounding Audit**:
   - Round raw order quantity according to selected mode (`FLOOR`, `CEIL`, `ROUND_NEAREST`).
2. **Minimum Fill Size (`MinQty`) Audit**:
   - Verify rounded quantity $\ge \text{min\_qty}$ and check available liquidity depth.
3. **Odd-Lot Policy Verification**:
   - Audit odd-lot compliance and populate FIX Tag 110 & Tag 1089.
4. **Audit Report Generation**:
   - Output structured order rounding report.
