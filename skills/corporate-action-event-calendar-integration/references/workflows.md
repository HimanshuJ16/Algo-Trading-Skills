# Workflows for Corporate Action Calendar Integration

1. **Feed Registration**:
   - Store corporate action events with 4 key dates: `declaration_date`, `ex_date`, `record_date`, `payment_date`.
   - Store the declared `ex_date_convention` alongside them. It is a property of the distribution's size relative to the security price, which this module cannot observe:
     - `PRE_RECORD` (default, <25% of value): `declaration <= ex <= record <= payment`. `ex == record` is the normal T+1 convention, not a data error.
     - `POST_PAYABLE` (>=25% of value — forward splits, large special dividends, spin-offs; FINRA Rule 11140(b)(2)): `declaration <= record <= payment <= ex`.
   - Reject events that fail construction validation: unknown event type, unknown convention, date ordering violating the declared convention, or non-positive/non-finite `value`.
   - Deduplicate on `event_id`: an identical re-broadcast already in the calendar is dropped with a warning (`register_event` returns `False`), never appended twice.
   - Decision point: if the re-broadcast **differs** from the stored event in symbol, event type, convention, any lifecycle date or value, it is an amendment (ISO 15022 MT 564 `REPL`), not a duplicate. It is still not applied — overwriting an ex-date that downstream sizing already acted on is its own hazard — but it is logged at ERROR naming the differing fields. Resolve against the golden source and re-register under a fresh identifier or a controlled replacement.
2. **Upcoming Risk Notification**:
   - Filter events where `ex_date` falls within target window $[T_{current}, T_{current} + N\text{ days}]$ (calendar days; no exchange holiday calendar — size the window for weekends/holidays).
   - The match is on ex-date, i.e. on when the price adjusts. For a `POST_PAYABLE` event that date is after the payable date, so the alert signals imminent price adjustment only — the entitlement cut-off (record date) is already behind you.
3. **Entitlement Accounting**:
   - Lock share count $N$ as of the close *preceding* the ex-date (buying on/after the ex-date creates no entitlement; selling on the ex-date still entitles the seller).
   - Recognize the receivable once the record date passes: $R = N \times \text{DividendPerShare}$, computed against the **latest** dividend whose record date has passed. Only one dividend is returned; $R$ is rounded to 2 decimal places and carries no currency tag.
   - Decision point: if a special dividend shares the regular dividend's record and payment dates, the "latest" lookup is ambiguous. The engine logs a warning and returns the lower `event_id` deterministically — accrue the remaining leg separately rather than trusting the single figure.
   - On `payment_date`, credit cash $R$ and close the receivable; confirm actual credit against custodian statements before treating `status == 'PAID'` as settled cash.
4. **Feed Reconciliation**:
   - Compare vendor feed $A$ vs vendor feed $B$ in **both directions** — flag events present in only one feed, duplicate `event_id`s within a feed, and mismatches in symbol, event type, ex-date convention, ex-date, record date, payment date, or dividend amount.
   - Symbol and event-type mismatches under a shared `event_id` matter as much as the dates: they are security-master mapping failures that would credit an entitlement to the wrong position.
   - Declaration-date differences are not flagged: they reflect vendor dissemination lag, not entitlement risk.
   - Decision point: any discrepancy found before the ex-date escalates to the golden source and holds automated processing for the affected symbol. Do not silently prefer either vendor's dates — a wrong ex-date silently corrupts both entitlement receivables and downstream position sizing.
