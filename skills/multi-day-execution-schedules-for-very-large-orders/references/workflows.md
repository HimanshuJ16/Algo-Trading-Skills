# Workflows for Multi-Day Execution Schedules

1. **Horizon Determination**:
   - Compute maximum daily volume allowance based on ADV participation limit.
   - Determine execution horizon $N_{\text{days}}$.
2. **Trajectory Slicing**:
   - Allocate daily slices $V_d$ according to selected profile (`EQUAL_DAILY`, `FRONT_LOADED`, `BACK_LOADED`).
3. **Impact & Risk Audit**:
   - Calculate temporary/permanent market impact and overnight risk.
4. **Audit Report Generation**:
   - Output structured multi-day execution schedule report.