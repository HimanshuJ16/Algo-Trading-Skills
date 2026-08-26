# Pre-Flight / Sign-off Checklist — margin-utilization-circuit-breaker

Use this before considering the skill's implementation complete.

- [ ] **Basis Declared:** Confirm `basis=` states whether `used_margin` is the *maintenance*
      or *initial* requirement, and that the broker field mapped in matches. Reg T initial
      (50%) and FINRA 4210(c) maintenance (25%) are roughly 2:1 apart — the wrong basis
      silently doubles or halves the budget.
- [ ] **Ratio Direction:** Confirm the adapter feeds `used_margin / equity` (higher is
      worse), not MetaTrader's `ACCOUNT_MARGIN_LEVEL` (`equity / margin × 100`, where
      *lower* is worse). Feeding the reciprocal in approves every order on a distressed
      account.
- [ ] **Thresholds Are Fractions:** Confirm `MarginUtilizationBreaker(60, 80)` raises
      `MarginDataError`, and that the configured values are $0.60$ / $0.80$ style fractions.
- [ ] **Threshold Ordering:** Confirm `warning < hard_stop`, `re_arm < hard_stop`, and — on
      the `MAINTENANCE` basis — `hard_stop < 1.0`. Tripping at $1.0$ maintenance is tripping
      after the broker's cushion is already gone.
- [ ] **Cushion Is Calibrated:** Confirm the hard-stop cushion was chosen against how fast
      your instruments gap and how your broker liquidates — IBKR does not issue margin
      calls and may liquidate an account that moved from a >10% cushion into violation
      without ever showing a warning.
- [ ] **Fails Closed on Bad Data:** Confirm NaN or infinite `used_margin`,
      `account_equity` and `additional_margin_required` each make `evaluate_margin` raise
      and `check_order` return `approved=False, is_data_error=True`. An approval from any
      of them is a fail-open bug.
- [ ] **Freshness Enforced:** Confirm `max_data_age_seconds` is set (not `None`), that
      `as_of` is timezone-aware and comes from when the broker value was *read*, and that a
      stale, naive or future-dated timestamp is refused. Confirm the poll cadence is
      tighter than the age limit.
- [ ] **Exact Boundaries:** Confirm behaviour at exactly $0.60$ and exactly $0.80$, not just
      mid-band.
- [ ] **Latch Holds:** Confirm that after tripping at 90%, an account back at 10% is *still*
      blocked and `is_latched` is `True`. Auto-recovery is a configuration error here, not
      a convenience.
- [ ] **De-Risking Is Never Blocked:** Confirm a margin-releasing order is approved while
      halted with `risk_reducing=True`, including a partial reduction that leaves the
      account over the limit — and that a margin-neutral order or reversal is still
      rejected. Confirm no caller-supplied "closing order" flag can bypass the gate.
- [ ] **Deficit Is Visible:** Confirm `margin_deficit` is read where a shortfall matters —
      `available_margin` is clamped at zero and hides the size of the gap. Confirm
      non-positive equity reports `math.inf` utilization and a cover-the-requirement
      deficit, not $1.0$.
- [ ] **Re-Arm Is a Real Gate:** Confirm `re_arm` returns a **checked** boolean, refuses a
      blank operator, a blank reason, stale input, and utilization above
      `re_arm_threshold`, and that every attempt — granted and refused — lands in
      `re_arm_log` and is persisted.
- [ ] **Audit Log Is Honest:** Confirm a *projected* rejection does not write a halt into
      the log for an account that is fine, and that a held halt is not re-logged CRITICAL on
      every poll.
- [ ] **Structural Independence:** Confirm the breaker lives outside the strategy module so
      a signal-logic bug cannot disable it, and that check-then-place is serialised at the
      caller.
- [ ] **Margin-Call Handling Is Separate:** Confirm `broker-account-margin-call-handling` is
      in place for broker-cushion grading and de-leveraging. This breaker prevents reaching
      a call; it does not manage one.
- [ ] **Requirement Drift Alerted:** Confirm there is an alert for a utilization jump no
      fill explains — clearing houses revise performance bonds by advisory notice with a
      stated effective date, and brokers layer house margin on top.
- [ ] **Automated Testing:** Run
      `python -m unittest discover -s skills/margin-utilization-circuit-breaker/scripts`
      and confirm a 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
