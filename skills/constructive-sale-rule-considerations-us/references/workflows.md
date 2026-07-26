# Workflows for Constructive Sale Rule

1. **Position Valuation**:
   - Calculate $\text{Unrealized Gain} = \text{FMV} - \text{Cost Basis}$.
   - If $\text{Unrealized Gain} \le 0 \implies$ Exit (Section 1259 does not apply to loss positions).
2. **Offsetting Transaction Audit**:
   - Detect offsetting short, equity swap, forward, or ITM put entry date $D_{entry}$.
3. **Safe Harbor Verification**:
   - Check 1: Short close date $D_{close} \le (\text{Tax Year End} + 30\text{ days})$.
   - Check 2: Unhedged duration following $D_{close} \ge 60\text{ days}$.
4. **Tax Result Generation**:
   - If Safe Harbor checks pass: `SAFE_HARBOR_QUALIFIED`.
   - If Safe Harbor checks fail: `CONSTRUCTIVE_SALE_TRIGGERED` on $D_{entry}$. Realize $\text{Gain} = \text{FMV}(D_{entry}) - \text{Cost Basis}$.