# Pre-Flight Checklist — Strategy-Specific Data Dependency Mapping

Strategy: ______________________  Map version: __________  Date: __________
Owner: ________________________  Risk reviewer: _______________________

## 1. Inventory completeness

- [ ] Every input the strategy reads at decision time is registered — including reference data,
      corporate actions, borrow availability, FX rates, and derived features, not only prices.
- [ ] Every `feed_id` matches the identifier the health probe actually reports.
- [ ] Every derived feed declares its `upstream_feed_ids`; the graph builds without a cycle,
      self-loop, or unknown upstream reference.
- [ ] The map was derived from deployed subscriptions and pipeline config, not from memory.

## 2. Freshness bounds

- [ ] Every feed has an explicitly chosen `max_acceptable_lag_seconds` (the engine provides no
      default — record who chose each bound and why).
- [ ] Each bound was derived from the feed's real publication cadence and the strategy's
      holding period, not copied from the tier table in `references/standards.md`.
- [ ] `future_timestamp_tolerance_seconds` matches the actual clock-discipline budget across
      the hosts producing and consuming these timestamps.
- [ ] Clock synchronisation on those hosts is monitored independently of this engine.

## 3. Vendor hierarchy

- [ ] Every feed's `vendors` list is ordered by genuine preference.
- [ ] `single_source_feeds()` has been run; each entry is either accepted in writing as a
      single point of failure or has a fallback scheduled.
- [ ] Each declared fallback is genuinely independent — separate upstream source, network,
      credentials, and parser — and is not a redistributor of the primary.
- [ ] **Every vendor in every hierarchy has a live health probe emitting `FeedObservation`s.**
      A fallback with no probe scores as unavailable and will not be there in an incident.

## 4. Failure responses

- [ ] Every feed's `BLOCK` / `DEGRADE` response was chosen from what the strategy actually does
      with a missing value, not from how important the feed feels.
- [ ] Every `DEGRADE` feed is an accepted, signed-off decision to trade on cached or imputed
      values for that input.
- [ ] No `DEGRADE` feed silently reuses a last-known value inside a price-sensitive calculation.

## 5. Policy

- [ ] The criticality weights, `fallback_credit`, `degraded_credit`, and
      `minimum_readiness_pct` in use are recorded with a rationale and an approver.
- [ ] It is documented that these values are operator-chosen and carry no regulatory basis.
- [ ] The policy is versioned with the rest of the risk configuration.

## 6. Integration

- [ ] `evaluate_strategy_readiness` is called before trading, on fresh observations, and the
      verdict is not cached across the session.
- [ ] The gate reads `is_strategy_ready_to_trade`, not a threshold re-derived from the score.
- [ ] Any exception from the engine is treated as **not ready** (fail closed).
- [ ] `blocked_dependencies` pages someone; `fallback_dependencies` and
      `degraded_dependencies` raise an alert; `warnings` is surfaced, not swallowed.
- [ ] Alert latency from a blocked dependency to a human is inside the budget the applicable
      real-time monitoring obligation imposes (see `references/standards.md`).

## 7. Portfolio and outage readiness

- [ ] `DataDependencyPortfolio.strategies_blocked_by(vendor)` has been run for every vendor and
      the results are published where an on-call responder can find them during an incident.
- [ ] Responders know that `assess_vendor_outage` assumes remaining vendors are healthy and is
      an upper bound on resilience — a live evaluation is authoritative during a correlated outage.

## 8. Verification

- [ ] `python -m unittest discover -s skills/strategy-specific-data-dependency-mapping/scripts`
      passes.
- [ ] A recorded historical outage was replayed through the engine and the verdict matches what
      the desk actually did.
- [ ] A reconciliation cadence against deployed configuration is scheduled and owned.

Sign-off — Owner: ____________________  Risk reviewer: ____________________
