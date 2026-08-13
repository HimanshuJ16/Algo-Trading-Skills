# Binary Options Pre-Deployment Checklist

Sign-off gate. Every unchecked box is a reason not to enable the strategy.

## Product scope

- [ ] Product classified by **function, not name** — digital, fixed-return, one-touch /
      no-touch, and binary-payout event contracts are all in scope.
- [ ] If the product is an event contract or prediction market, its status as a MiFID
      financial instrument has been assessed with counsel (ESMA statement, 3 July 2026).
- [ ] Confirmed the firm entity is authorised to distribute the product in each target
      market — separately from whether the product itself is permitted.

## Regulatory rule table

- [ ] `RULESET_LAST_VERIFIED` reflects a check actually performed against **primary
      regulator sources**, not a secondary summary, and not a date bumped to silence the
      staleness warning.
- [ ] Named owner and review cadence assigned, at or inside `ruleset_max_age`.
- [ ] No document or code comment asserts ESMA's EU-wide ban as current — it expired
      1 July 2019; binding measures are national under MiFIR Article 42.
- [ ] For EU exposure, per-member-state measures supplied via
      `ComplianceEngine(jurisdiction_rules=...)`; the built-in `EU_ESMA` member is
      understood to be non-determinative for non-retail.
- [ ] UK rule cites **COBS 22.4**, not COBS 22.6 (which is cryptoasset derivatives).
- [ ] Canadian rule keyed on **individual + term to maturity < 30 days**, not on
      retail/professional; British Columbia carve-out considered.
- [ ] Every jurisdiction the firm trades has a rule configured — unconfigured
      jurisdictions are denied by default, and that default has been tested.

## Client and venue facts

- [ ] Client categorisation feed is reliable, and `client_type` is never assembled from
      free text without normalisation.
- [ ] `is_natural_person` populated wherever Canadian clients are in scope; understood
      that unknown fails closed.
- [ ] Venue registration list is **dated configuration with a named owner**, checked
      against the regulator's current list — no venue names hardcoded in the compliance
      module.
- [ ] US venues verified against the CFTC's current DCM list; CFTC RED List checked for
      unregistered foreign entities.
- [ ] `venue_status` is populated from that registry on every order; understood that
      `UNKNOWN` is denied.

## Input integrity

- [ ] All expiry timestamps are timezone-aware; naive timestamps rejected, not coerced.
- [ ] Notional and strike validated finite and positive — NaN, Inf, zero, and negative
      all rejected at construction.
- [ ] Unrecognised client categories raise rather than falling through to a permissive
      branch.
- [ ] Already-expired options rejected.

## Risk limits

- [ ] Per-trade notional cap set from the firm risk mandate.
- [ ] Aggregate book cap set (`max_aggregate_notional`) — it is `None`/disabled by
      default.
- [ ] `max_pin_risk_exposure` and `pin_window` calibrated, and understood to be a
      **notional concentration cap, not a Greeks-based measure**.
- [ ] Greeks/delta-gamma management near the strike handled separately — see
      `options-pin-risk-management-at-expiry`.
- [ ] Stress and VaR scenarios assume the **full discontinuous loss**, not an interpolated
      payoff.
- [ ] `asset_id` is a stable per-order identifier so retries replace rather than
      double-count exposure.
- [ ] `release_trade` wired to settlement / expiry / cancellation so the book does not
      grow unbounded.
- [ ] Concurrency reviewed if the gate is called from multiple threads.

## Audit and operations

- [ ] Decision records persisted with `reason_code`, `citation`, evaluated timestamp, and
      input context — **rejections included**, since they are the evidence the gate ran.
- [ ] `binary_options` logger routed to the retention pipeline; retention period set per
      `record-retention-periods-by-jurisdiction`.
- [ ] Staleness warnings alert somewhere a human reads, not just a log file.
- [ ] Legal sign-off recorded, naming the jurisdictions and client categories covered and
      those explicitly out of scope.
- [ ] Full test suite run and passing, including the retail-capitalisation, NaN-notional,
      unknown-venue, and Canadian-boundary cases.
