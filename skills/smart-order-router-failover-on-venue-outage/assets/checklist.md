# Pre-Flight Checklist — SOR Venue Outage Failover

## Inputs
- [ ] Is a fresh top-of-book quote pushed for every venue via `update_quote()`, so `quote_monotonic_ts` is stamped?
- [ ] If venue fields are mutated directly, is the timestamp stamped from `time.monotonic()` — the same clock the staleness check reads?
- [ ] Are transport faults (FIX session reject, gateway timeout, disconnect, HTTP 5xx) wired to `report_venue_error()`?
- [ ] Are fills, acks and heartbeats wired to `report_venue_success()`?
- [ ] Are **business** rejects (buying power, bad symbol, locked/crossed limit, failed risk check) kept *out* of the breaker?

## Quote validity
- [ ] Is a venue with a non-positive or `NaN` side price excluded rather than treated as the cheapest venue?
- [ ] Is `max_quote_age_seconds` calibrated to this feed, not left at the 1 s default?
- [ ] Is `require_quote_timestamp=True` set for production, so an undated venue is excluded rather than assumed fresh?
- [ ] Is `stale_quote_check_skipped` empty on every live route — i.e. is every venue actually timestamped?
- [ ] Is `QUOTE_TIMESTAMP_IN_FUTURE` absent from `excluded_venues` — i.e. is nothing stamping quotes from `time.time()` instead of `time.monotonic()`?

## Breaker and recovery
- [ ] Is `max_error_threshold` calibrated against this venue's measured timeout and reject base rate, not left at 3?
- [ ] Does a success arriving while the circuit is open leave it **open**?
- [ ] Is `refresh_venue_states()` driven by a timer as well as by routing, so state advances in a quiet market?
- [ ] Is the recovery probe a session heartbeat or test request where the venue supports one, rather than a live customer order?
- [ ] Is `max_cooldown_seconds` set so a venue down for the session is not probed with a live order all day?

## Routing decisions
- [ ] Do `RECOVERY_PROBE` venues rank last regardless of price?
- [ ] Does a `DEGRADED` venue at a better price still win, so health is only a tie-break?
- [ ] Is every use of `preferred_venue_id` justifiable, and is `price_improvement_forgone` reviewed when non-zero?
- [ ] Is `side` validated to `BUY`/`SELL`, so a typo cannot execute as the opposite side?

## Outage response
- [ ] Does `suspected_local_fault` page a human, and does the runbook check local NIC, DNS, credentials, clock skew and firewall **before** exchange status pages?
- [ ] Is `NoEligibleVenueError` handled with a decided action — halt, queue, or manual desk — rather than propagating into an unhandled exception with a live parent order?
- [ ] Are backup venue sessions pre-warmed and logged in, so failover does not begin with a FIX logon?
- [ ] Are in-flight orders at the failed venue reconciled against drop copy **before** any residual is re-routed?
- [ ] Is `unrouted_quantity` explicitly consumed by the caller, never assumed to be zero?

## Compliance and audit
- [ ] Are the objective bypass parameters documented in advance, as Rule 611(a)(1) policies and procedures require of a trading center electing self-help?
- [ ] If you are a **trading center** under 17 CFR 242.600(b)(106), is the bypassed venue notified at or immediately after election of self-help?
- [ ] Is the full `SORRoutingResult` persisted — `excluded_venues`, `fallback_venues_used`, `price_improvement_forgone`, `audit_notes` — not just the target venue?
- [ ] Do venue outage and rejection statistics feed the FINRA Rule 5310 Supplementary Material .09 regular and rigorous review, at least quarterly?
- [ ] For an EU/UK firm: are these arrangements documented in a durable medium and tested annually, per RTS 6 Article 14?

## Sign-off
- [ ] Has a venue outage been rehearsed end-to-end against a test gateway, including recovery, not just unit-tested?
- [ ] Are the thresholds in this deployment recorded as *engineering* defaults, with no unsourced regulatory SLA claimed for them?
