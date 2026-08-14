# Pre-Flight / Sign-off Checklist — broker-account-margin-call-handling

Use this before considering the skill's implementation complete.

- [ ] **Ratio Evaluation:** Confirm `evaluate_margin_health()` calculates both initial and maintenance margin ratios accurately.
- [ ] **Broker Cushion Cross-Check:** Confirm a snapshot with a *healthy* house ratio but
      **negative `excess_liquidity`** escalates to `MARGIN_CALL_BREACH` with
      `broker_deficiency` set. Equity with Loan Value is not NLV, and it is the broker's
      cushion that triggers liquidation.
- [ ] **Fail-Closed on Bad Data:** Feed NaN and infinity into each snapshot field in turn
      and confirm `MarginDataError` every time. Any `NORMAL` result here is a fail-open bug.
- [ ] **Non-Positive Equity:** Confirm NLV $\le 0$ yields `HALT_AND_ESCALATE` (not
      `DE_LEVERAGE_IMMEDIATELY`) and that the reported deficit uses the true NLV, not a floor.
- [ ] **Multi-Tier Gates:** Confirm state transitions at the **exact** boundaries — 85.0%,
      95.0%, 100.0% — not just mid-band values.
- [ ] **Threshold Config Validation:** Confirm unordered thresholds and a de-leverage target
      at or above the breach threshold are rejected at construction.
- [ ] **Predictive Order Veto Gate:** Confirm `guard_new_order()` blocks leverage-increasing
      orders under margin stress, and that it **vetoes on initial margin** when
      `initial_margin_impact > available_funds` even if the maintenance projection passes.
- [ ] **Bypass Integrity:** Confirm an order flagged `is_deleveraging=True` with a *positive*
      margin impact is refused rather than exempted.
- [ ] **Margin Impact Source:** Confirm impacts come from the broker (IBKR: `Order.whatIf`
      → `OrderState.initMarginChange` / `maintMarginChange`), not from a local estimate.
- [ ] **Tail-Risk Prioritization:** Confirm `plan_deleveraging()` sorts unhedged short options first for liquidation.
- [ ] **Liquidity Capping:** Confirm `plan_deleveraging()` applies an ADV (Average Daily Volume) maximum participation rate to prevent crashing illiquid assets, and that `average_daily_volume` is supplied per position rather than defaulted.
- [ ] **Deficit Actually Cleared:** Confirm the capped plan is checked against the required
      reduction, and that a shortfall escalates instead of being assumed handled.
- [ ] **Portfolio Margin / SPAN Re-Pricing:** If on portfolio-level margining, confirm each
      slice is re-priced through the broker before sending — closing a hedge leg can
      *increase* the requirement.
- [ ] **Scheduled Square-Off:** Confirm the bot models broker time-based square-offs
      (e.g. Zerodha MIS around 15:20 IST), not only margin thresholds.
- [ ] **Automated Testing:** Run `python -m unittest discover -s skills/broker-account-margin-call-handling/scripts` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
