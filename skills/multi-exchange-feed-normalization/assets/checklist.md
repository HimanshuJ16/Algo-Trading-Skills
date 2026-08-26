# Pre-Flight / Sign-off Checklist — multi-exchange-feed-normalization

Use this before considering the skill's implementation complete.

## Schema boundary

- [ ] **Unified Data Model:** Every parser returns `UnifiedTick`; no venue-specific type
      crosses the boundary.
- [ ] **No Leaked Fields:** Grep strategy and feature code for raw venue keys (`s`, `p`,
      `q`, `product_id`, `last_price`). Any hit means the boundary is not a boundary.

## Aggressor side

- [ ] **Convention Confirmed Per Venue:** For each venue, the side field's meaning was
      read in that venue's own documentation — not inferred from the data.
- [ ] **Maker Fields Inverted:** Coinbase publishes the *maker* side and is inverted;
      Binance `m=true` yields `SELL`.
- [ ] **Cross-Venue Agreement Tested:** One economic event (a resting bid being hit)
      produces the same `NormalizedSide` on every venue that reports one.
- [ ] **`UNKNOWN` Handled Downstream:** Consumers exclude `UNKNOWN` from signed sums
      rather than defaulting it to a side. Every Zerodha tick is `UNKNOWN`.

## Symbols

- [ ] **Mappings Registered:** Every (venue, ticker) pair the feed can emit is mapped
      before the feed starts.
- [ ] **Strict Mode On:** `strict_symbols=True` in production, or a written reason why
      not.
- [ ] **Consolidation Verified:** The same instrument from two venues resolves to one
      canonical symbol.

## Timestamps

- [ ] **Units Resolved, Not Guessed:** Seconds, milliseconds, microseconds and
      nanoseconds all resolve to the same instant; an out-of-band value raises.
- [ ] **Microsecond Streams Checked:** If any Binance connection carries
      `timeUnit=MICROSECOND`, confirm timestamps land in the present, not the far future.
- [ ] **Naive Zone Declared:** `naive_timestamp_tz` matches the feed. `None` (host
      local) for `pykiteconnect` objects.
- [ ] **Arrival Time Captured At Read:** `receipt_timestamp` is passed in wherever a
      queue sits between the socket and the parser.

## Failure behaviour

- [ ] **Nothing Is Defaulted:** Missing price, `nan` price, zero quantity, missing
      timestamp and unmapped symbol each raise `NormalizationError` — no tick is
      produced.
- [ ] **Single Catch Type:** The feed handler catches `NormalizationError` at the venue
      boundary and dead-letters the payload.
- [ ] **No Retry On Rejection:** Rejected payloads are not re-parsed.
- [ ] **Rejection Rate Alerted:** A rising rejection rate pages someone; it usually
      means a venue changed its schema.

## Testing

- [ ] **Automated Testing:** Run
      `python -m unittest discover -s skills/multi-exchange-feed-normalization/scripts`
      and confirm all tests pass.
- [ ] **Regression Coverage:** Tests marked `REGRESSION` are present and fail against the
      pre-2.0 behaviour.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
- Venues in scope and side convention verified for each: ___________________________
