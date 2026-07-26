# Workflows for Corporate Action Calendar Integration

1. **Feed Registration**:
   - Store corporate action events with 4 key dates: `declaration_date`, `ex_date`, `record_date`, `payment_date`.
2. **Upcoming Risk Notification**:
   - Filter events where `ex_date` falls within target window $[T_{current}, T_{current} + N\text{ days}]$.
3. **Entitlement Accounting**:
   - On `record_date`, lock share count $N$.
   - Record dividend receivable: $R = N \times \text{DividendPerShare}$.
   - On `payment_date`, credit cash $R$ and close receivable.
4. **Feed Reconciliation**:
   - Compare vendor feed $A$ vs vendor feed $B$.
   - Flag discrepancies in ex-date or dividend amount.
