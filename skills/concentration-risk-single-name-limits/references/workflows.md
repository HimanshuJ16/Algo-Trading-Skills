# Workflows for Single-Name Concentration Limits

1. **Portfolio Data Ingestion**:
   - Fetch total NAV, current positions (market values), and 20-day ADV per symbol.
2. **Pre-Trade Evaluation Workflow**:
   - Calculate max allowed shares for NAV limit: $N_{nav} = \frac{(\text{Max\_NAV\_Pct} \times \text{NAV}) - \text{Current\_Val}}{\text{Price}}$.
   - Calculate max allowed shares for ADV limit: $N_{adv} = \text{Max\_ADV\_Pct} \times \text{ADV}$.
   - Allowed Quantity $N_{allowed} = \max(0, \lfloor\min(N_{nav}, N_{adv})\rfloor)$.
3. **Action Execution**:
   - If Proposed Qty $\le N_{allowed}$: Pass order through without modification.
   - If Proposed Qty $> N_{allowed}$: Downsize order to $N_{allowed}$ (or reject if downsizing disallowed).
4. **HHI Reporting**:
   - Calculate weights $w_i = \text{Position\_Val}_i / \text{Gross\_Portfolio\_Val}$.
   - Compute $HHI = \sum w_i^2$ and $N_{eff} = 1 / HHI$.
