# Pre-Flight / Sign-off Checklist — portfolio-stress-test-including-liquidity-crunch-scenarios

## Input data
- [ ] Positions are netted to **one row per instrument** — DTL is a property of the
      aggregate holding, not of a row.
- [ ] `current_price` and `adv_shares` are in the **same unit** (price per contract with
      ADV in contracts for derivatives).
- [ ] The ADV window is stated, and checked against holiday stretches, expiries and
      single block or index-rebalance prints that inflate the mean.
- [ ] Non-finite (`NaN`/`inf`), numeric-string, non-positive price and non-positive ADV
      inputs are rejected, not repaired.
- [ ] `daily_volatility`, where supplied, is a **stressed** volatility consistent with the
      shock — not a calm-period estimate.

## Scenario definition
- [ ] Every symbol has a shock, or a `DEFAULT` key is set deliberately.
- [ ] `liquidity_drop_pct` is calibrated as a loss of **absorbable size at a tolerable
      price**, not as a forecast of tape volume — crash-period volume typically rises
      while depth collapses.
- [ ] `spread_expansion_factor` is calibrated, and hypothetical severities are run
      alongside historical ones (ESMA34-39-897 para. 45).
- [ ] Offsetting legs are shocked consistently, or deliberately shocked apart if the
      hedge is not believed to hold in a crunch.
- [ ] More than one severity has been run; a single scenario is a data point.

## Policy configuration
- [ ] `daily_participation_rate`, `max_allowed_dtl_days` and `impact_coefficient_y` are
      validated at construction and their values recorded with a rationale.
- [ ] It is understood that all defaults ($10\%$, $5$ days, $Y = 1.0$, $50\%$ drop,
      $5\times$ spreads) are **library defaults, not regulatory limits**.
- [ ] `max_allowed_dtl_days` is calibrated to the horizon the book must actually survive
      (margin cycle, redemption terms, funding line).
- [ ] Where a regulatory framework genuinely applies (ESMA34-39-897 for UCITS/AIF
      managers; SEC Rule 22e-4 for US registered open-end funds), it is satisfied
      separately — this engine does not implement either.

## Results review
- [ ] Price shock loss, spread cost and market impact are read **separately**, not as one
      number.
- [ ] `net_exposure_usd` and `total_portfolio_value_usd` (gross) are both reviewed — the
      haircut is charged on gross.
- [ ] `positions_missing_volatility` is checked; those positions contributed **zero**
      impact cost and the haircut is a lower bound.
- [ ] `positions_outside_impact_calibration` is checked before any impact figure is
      quoted; beyond $\phi = 0.10$ it is an extrapolation, not a cost estimate.
- [ ] The bottleneck warning is escalated on its own merits — a book can pass the P&L
      test and still be untradeable.
- [ ] Long-$DTL$ positions are routed to a sizing remedy
      (`liquidity-adjusted-position-sizing`), not just noted.

## Audit
- [ ] The full `StressTestReport`, including the per-position `positions` breakdown, is
      persisted.
- [ ] The scenario parameters are stored with the result — a stressed loss without the
      shock, capacity haircut and spread expansion that produced it cannot be reviewed.
- [ ] Engine version and configuration are recorded alongside the run.

## Scope
- [ ] Correlation, crowding, margin spirals and forced-seller feedback are handled
      elsewhere; this engine applies shocks independently per symbol.
- [ ] Funding, margin and settlement timing are out of scope.
- [ ] The haircut is a fire-sale estimate, not a pre-trade cost forecast.

## Testing
- [ ] Automated Testing: Run
      `python -m unittest discover -s skills/portfolio-stress-test-including-liquidity-crunch-scenarios/scripts`
      — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
