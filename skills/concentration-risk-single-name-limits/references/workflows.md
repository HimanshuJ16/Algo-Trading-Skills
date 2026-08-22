# Workflows for Single-Name Concentration Limits

1. **Portfolio Data Ingestion**:
   - Fetch total NAV, current **signed** position market values (negative for shorts), and ADV per symbol over the firm's configured lookback window.
   - Fetch the signed notional of orders already sent to a venue for each symbol and not yet filled or cancelled.
   - Fold single-stock-future and option delta notional into the position value if those exposures share the single-name limit.

2. **Effective Exposure**:
   - $E = \text{Current\_Signed\_Val} + \text{Pending\_Order\_Notional}$.
   - Omitting the pending term is the classic breach path: $n$ concurrent orders each pass the limit individually and breach it collectively.

3. **NAV Headroom** — cap the **absolute** resulting exposure at $L = \text{Max\_NAV\_Pct} \times \text{NAV}$:
   - Order increases $|E|$ (order side matches the sign of $E$, or $E = 0$):
     $\text{headroom} = L - |E|$, floored at 0. Already at or beyond $L$ means no increase is approved.
   - Order reduces $|E|$ (opposite side):
     $\text{headroom} = |E| + L$ — the full unwind plus a compliant position on the far side. A de-risking trade is never blocked.
   - $N_{nav} = \max(0, \lfloor \text{headroom} / \text{Price} \rfloor)$.

4. **ADV Headroom**:
   - $N_{adv} = \lfloor \text{Max\_ADV\_Pct} \times \text{ADV} \rfloor$, applied to buys and sells alike.
   - A missing, zero, or negative ADV means the liquidity limit **cannot be evaluated**. Reject the order. Never treat absent data as an absent constraint.

5. **Action Execution**:
   - $N_{allowed} = \min(N_{nav}, N_{adv})$.
   - If Proposed Qty $\le N_{allowed}$: pass the order through without modification.
   - If Proposed Qty $> N_{allowed} > 0$: downsize to $N_{allowed}$, or reject if downsizing is disallowed.
   - If $N_{allowed} = 0$: reject. Always floor share counts — rounding up crosses the limit.
   - Apply lot-size and minimum-fill rounding downstream before routing; this module returns a raw share count.

6. **HHI Reporting**:
   - Calculate weights $w_i = |\text{Position\_Val}_i| / \text{Gross\_Portfolio\_Val}$, so a long and an equal short count as two gross positions rather than netting to zero. Feed net values instead if the risk policy nets them.
   - Compute $HHI = \sum w_i^2$ and $N_{eff} = 1 / HHI$.
   - Zero gross exposure yields NaN for both: concentration is undefined for an empty portfolio, and NaN prevents it reading as either "fully concentrated" or "maximally diversified" in downstream alerting.
