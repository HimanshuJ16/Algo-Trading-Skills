# Workflows for Corporate Action Calendar Integration

1. **Feed Registration**:
   - Store corporate action events with 4 key dates: `declaration_date`, `ex_date`, `record_date`, `payment_date`.
   - Reject events that fail construction validation: unknown event type, `declaration > ex`, `ex > record`, `record > payment` (remember `ex == record` is the normal T+1 convention, not a data error), or non-positive/non-finite `value`.
   - Deduplicate on `event_id`: a re-broadcast already in the calendar is dropped with a warning (`register_event` returns `False`), never appended twice.
2. **Upcoming Risk Notification**:
   - Filter events where `ex_date` falls within target window $[T_{current}, T_{current} + N\text{ days}]$ (calendar days; no exchange holiday calendar — size the window for weekends/holidays).
3. **Entitlement Accounting**:
   - Lock share count $N$ as of the close *preceding* the ex-date (buying on/after the ex-date creates no entitlement; selling on the ex-date still entitles the seller).
   - Recognize the receivable once the record date passes: $R = N \times \text{DividendPerShare}$, computed against the **latest** dividend whose record date has passed.
   - On `payment_date`, credit cash $R$ and close the receivable; confirm actual credit against custodian statements before treating `status == 'PAID'` as settled cash.
4. **Feed Reconciliation**:
   - Compare vendor feed $A$ vs vendor feed $B$ in **both directions** — flag events present in only one feed, duplicate `event_id`s within a feed, and mismatches in ex-date, record date, payment date, or dividend amount.
   - Declaration-date differences are not flagged: they reflect vendor dissemination lag, not entitlement risk.
   - Decision point: any discrepancy found before the ex-date escalates to the golden source and holds automated processing for the affected symbol. Do not silently prefer either vendor's dates — a wrong ex-date silently corrupts both entitlement receivables and downstream position sizing.
