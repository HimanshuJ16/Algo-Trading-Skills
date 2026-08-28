# Workflows for Real-Time vs Delayed Data Entitlement Handling

## 1. Build the venue delay policy table (once, then on every policy change)

1. For each venue whose **delayed** feed you serve, open that venue's published
   market data policy and read off its real-time/delayed boundary. Do not copy a
   number from another venue: CME Group and ICE Futures Europe draw the line above
   ten minutes, Nasdaq and the ESMA terminology at fifteen.
2. Encode it as a `VenueDelayPolicy`:
   - `min_delay_minutes` — the smallest whole-minute delay that clears the
     boundary (15 for Nasdaq/ESMA, 11 where the venue requires *more than* ten).
   - `delay_minutes` — the delay your throttle actually applies. It must be at
     least `min_delay_minutes`; the engine refuses the stream otherwise.
   - `max_delay_minutes` — where the venue caps delayed data (CME: 479 minutes,
     below eight hours). Leave `None` where the ceiling is untracked.
   - `display_label` — the Prominent Delay Message, verbatim, and consistent with
     `delay_minutes`. A label reading "Data Delayed 15 minutes" on an 11-minute
     feed misinforms the screen it appears on.
   - `policy_source` — document name and version, so a decision can be traced back
     to the paperwork.
3. Reconcile the table against the executed Order Forms and ILA schedules on a
   schedule, so drift surfaces before an audit does. Venues change these terms:
   CME reclassified end-of-day data as delayed in 2025.

## 2. Gate each market data request

1. Call `evaluate_request(user, request)` **before** the stream is opened, not
   after the first tick has been consumed.
2. Treat `EntitlementConfigurationError` as a defect to fix, not a denial to log
   and continue past.
3. Route on `status`, and treat any value outside `ALL_STATUSES` as a denial.
   Never route on the absence of a specific status.
4. Persist the returned `EntitlementAuditReport` — denials included — before the
   stream is used.

## 3. Execution compliance

1. Set `is_trading_execution_request=True` for every request whose data will
   reach an order-entry path, including pre-trade risk checks, auto-hedgers and
   smart order routers — not only alpha strategies.
2. On `LIVE_TRADING_BLOCKED_DELAYED_DATA`, stop. Do not degrade to "trade anyway
   with a wider limit": the price the decision is based on is stale by the
   venue's whole delay interval.
3. Escalate the block as an entitlement gap — the usual root cause is a missing
   real-time licence for that venue, which someone has to buy.

## 4. Display obligations for delayed streams

1. Render `required_display_label` prominently on every surface showing the data,
   "at or near the top of the page" — including wall boards, tickers, mobile
   views and audio responses.
2. On a scrolling display, re-show the message at least every
   `delay_message_refresh_seconds` (Nasdaq: 90 seconds).
3. Keep the label and the applied delay in sync; both come from the same policy
   record precisely so they cannot drift apart.

## 5. Audit report handling

1. Store `user_id`, `symbol`, `exchange`, `status`, `audit_notes`,
   `entitlement_tier`, `subscriber_type`, `delay_minutes` and `policy_source`.
2. Retain for at least the audit look-back period (three years under the Nasdaq
   Global Data Agreement).
3. Reconcile approvals against the venue reports your distributor files, so an
   approval that was never reported as a unit of count is caught internally
   first.
