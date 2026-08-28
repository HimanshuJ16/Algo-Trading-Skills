# Pre-Flight Checklist — Smart Order Routing Across Venues

US NMS stocks only. Not applicable to listed options (options linkage plan) or
non-US venues.

## Scope

- [ ] Instrument is an NMS stock, not a listed option or a non-US listing.
- [ ] The quote snapshot carries only *automated* exchange BBO quotes. Manual and
      non-firm quotes are filtered upstream — `VenueQuote` has no `is_automated`
      flag and cannot tell the difference.
- [ ] All venue quotes are from the same instant, with the capture timestamp
      recorded for audit.

## Input integrity

- [ ] `price_increment` matches the instrument's tick size (\$0.01 at ≥\$1.00,
      \$0.0001 below \$1.00) — not left at the default by accident.
- [ ] One quote per `venue_id`; no duplicates.
- [ ] Prices and sizes are finite; sides with no displayed size use `qty=0`.
- [ ] `side` is exactly `'BUY'` or `'SELL'`; `quantity` is finite and > 0.
- [ ] Per-venue `taker_fee_per_share` reflects the venue's **current published**
      schedule and your firm's tier — not the illustrative `VenueQuote` defaults.

## Price integrity

- [ ] Price comparison runs on the tick grid. No `==` on raw floats anywhere in
      the routing path.
- [ ] `best_quoted_price == nbbo_price` on the tick grid. If they differ, the
      zero-size-better-quote warning was investigated and the snapshot confirmed
      fresh **before** routing.
- [ ] `locked_or_crossed` is False, or the dislocation was deliberately accepted.
- [ ] `limit_price` is set, or an unbounded sweep is a conscious, recorded choice.

## Trade-through and ISO

- [ ] No child order is priced inferior to any venue in the snapshot quoting
      better with displayed size.
- [ ] If `iso_required_for_remainder` is True, the remainder is **not** filled at
      an inferior price unless the fill is ISO-marked **and** accompanied by
      simultaneous limit orders against the full displayed size of every superior
      protected quotation (17 CFR 242.600(b)(47)). An ISO tag alone is not an
      exemption.
- [ ] Second-wave routing decisions use live protected-quote state, not the
      snapshot this plan was built from.

## Fees

- [ ] Taker fees are within the operative Rule 610(c) access fee cap, or each
      exception is explained (off-exchange venue, non-protected quotation).
- [ ] `access_fee_cap_per_share` matches the cap in force — re-verified against
      the SEC, since the 2024 amendment to \$0.0010/share has a deferred
      compliance date. Not hard-coded.
- [ ] `net_expected_cost_usd` is read as cash paid (BUY) / cash received (SELL),
      both positive — not as signed P&L.
- [ ] No one is expecting `maker_rebate_per_share` to influence routing. It does
      not; this engine plans taking, not posting.

## Dispatch and audit

- [ ] Child orders are dispatched **concurrently**, not serially.
- [ ] Each child carries an idempotent client order ID
      (`order-placement-idempotency`).
- [ ] Venue reachability is handled separately from venue pricing
      (`smart-order-router-failover-on-venue-outage`).
- [ ] Snapshot, `price_increment`, full plan and `audit_notes` are persisted so
      the routing decision is reproducible for a best-execution or CAT enquiry.
