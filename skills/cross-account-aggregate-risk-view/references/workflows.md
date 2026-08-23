# Workflows for Cross-Account Aggregate Risk View

1. **Sub-Account Ingestion**:
   - Parse positions $\{s: Q_a(s)\}$ and balances $(\text{Cash}_a, \text{Margin}_a)$ for all accounts $a$.
   - Records validate on construction (finite balances, non-negative margin used/limits, finite quantities); malformed feeds raise `ValueError` instead of partially aggregating. Update an account by re-registering it — the new record replaces the old one wholesale.
2. **Consolidation**:
   - $Q_{net}(s) = \sum_a Q_a(s)$.
   - $\text{NAV}_{firm} = \sum_a \text{Cash}_a + \sum_s Q_{net}(s) \cdot P(s)$.
   - $\text{GMV}_{firm} = \sum_s |Q_{net}(s) \cdot P(s)|$ — gross across symbols, netted within each symbol.
   - **Fail-closed pricing**: a held symbol whose price is missing, zero, negative, or NaN cannot be valued. It is listed in `unvalued_symbols`, produces a compliance violation, and blocks pre-trade approval — totals are understated, never silently computed against $0.00.
3. **Internal Offsetting Audit**:
   - Find symbols with concurrent long and short positions across sub-accounts ($Q_{a1}(s) > 0 \wedge Q_{a2}(s) < 0$, including fully-netting pairs).
   - Flag as `INTERNAL_OFFSETTING_FRICTION`. This is a capital-efficiency flag (double borrow, commission, and margin drag), does not affect `is_compliant`, and is not itself a regulatory wash trade — wash-trade rules attach to *executions* without change of beneficial ownership, not to static offsetting holdings.
4. **Pre-Trade Firm-Wide Check** (`evaluate_pre_trade_order`):
   - Evaluate proposed order in account $A$ on the exact post-trade book: add the signed quantity to the account's current position and value the traded symbol at the live order price.
   - If projected $\text{GMV}_{firm} > \text{Max GMV Limit}$ or projected margin utilization exceeds its cap $\implies$ return `(False, reason)`. There is no automatic downsizing; the caller sizes any retry.
   - Unknown `account_id` returns `(False, reason)`; non-finite quantity or non-positive price raises `ValueError`.
   - Risk-reducing orders are judged on their projected net position and may be approved even while the firm is currently over a cap — de-risking is never frozen by an existing breach.
