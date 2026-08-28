# Pre-Flight Checklist

## Venue delay policy

- [ ] Is there a `VenueDelayPolicy` for every venue whose delayed feed is served — with no venue silently falling back to a 15-minute house default?
- [ ] Is `min_delay_minutes` taken from that venue's own definition (Nasdaq/ESMA 15; CME and ICE require more than ten minutes, so 11), not copied between venues?
- [ ] Does the configured `delay_minutes` match what the throttle actually applies in production?
- [ ] Is `max_delay_minutes` set where the venue caps delayed data (CME: below eight hours), so an over-delayed feed is not served under a delayed licence?
- [ ] Does `display_label` state the same delay the feed actually carries?
- [ ] Is `policy_source` populated with the document and version, and re-checked when the venue republishes its policy?

## Tiering decisions

- [ ] Does every consumer pass through the gate *before* the stream is opened?
- [ ] Is `entitlement_tier` sourced from the entitlement system in the engine's vocabulary (`REAL_TIME` / `DELAYED`), with unrecognised values surfacing as denials rather than being coerced?
- [ ] Does the caller treat any unrecognised `status` as a denial rather than falling through to approval?
- [ ] Is `EntitlementConfigurationError` handled as a defect to fix, not swallowed?

## Execution safety

- [ ] Is `is_trading_execution_request` set for every path that can reach order entry — risk checks, auto-hedgers and routers included, not just alpha strategies?
- [ ] Is `LIVE_TRADING_BLOCKED_DELAYED_DATA` a hard stop, with no "trade anyway" degradation path?
- [ ] Is a real-time entitlement understood as a licence, not evidence the feed is currently fresh — with feed staleness monitored separately?

## Display obligations

- [ ] Is `required_display_label` rendered prominently on every surface showing delayed data, including tickers, mobile views and audio responses?
- [ ] Does a scrolling display re-show it at least every `delay_message_refresh_seconds` (Nasdaq: 90 seconds)?

## Audit evidence

- [ ] Is every `EntitlementAuditReport` — denials included — persisted durably as it is returned?
- [ ] Does retention cover the audit look-back period (three years under the Nasdaq Global Data Agreement)?
- [ ] Is automated consumption of *delayed* data still declared where the venue requires it (CME reports Non-Display Use of Real Time and Delayed Information per Application)?
- [ ] Are approvals reconciled against the units of count your distributor actually reports to each venue?
