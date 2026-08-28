# Pre-Flight Checklist — stress-testing-against-historical-crash-scenarios

## Scenario library

- [ ] Library includes at least 3 historical episodes relevant to the book actually held.
- [ ] Every scenario records `window_start`, `window_end` and `basis`.
- [ ] Bases are consistent, or the inconsistency is stated — peak-to-trough, single-day
      intraday and calendar-window returns are different quantities and must not be ranked
      against each other silently.
- [ ] For each asset it was decided, and recorded, whether the shock is the window return
      or the asset's own adverse move inside the episode. Hedge legs bottom on different
      dates than the equity leg.
- [ ] Every single-name shock is sourced from point-in-time data, and each symbol's listing
      date precedes `window_start`. No shock exists for a pre-IPO period.
- [ ] The shock vector was built from point-in-time constituents, including names that were
      delisted or went to −100%, not from today's survivors.
- [ ] `DEFAULT` fallback is present only if unnamed symbols should be stressed by
      assumption, and its magnitude is documented.
- [ ] Built-in scenario magnitudes were recalibrated, or the decision to ship the defaults
      is recorded with a reason. They are library defaults, not reconstructions.

## Inputs

- [ ] Quantities are **signed** — shorts are negative. No sign inversion workaround remains
      from version 1.0.0.
- [ ] `portfolio_nav` is the capital base, not net exposure, and is positive and finite.
- [ ] Every held, non-flat symbol has a price.
- [ ] `max_stressed_loss_pct` is calibrated to the capital the book must not lose, and the
      calibration is recorded. The 0.15 default is not a regulatory limit.

## Reading the report

- [ ] `report.status` was read **before** any P&L figure was quoted.
- [ ] `unpriced_symbols`, `unshocked_symbols` and `fallback_symbols` are all empty, or the
      gaps are explained and documented alongside the number.
- [ ] A $0 stressed loss was investigated, not accepted — it means either nothing moved or
      nothing matched.
- [ ] The worst case was read from the signed `worst_pnl_pct`; a scenario gain is not
      reported or escalated as a loss.
- [ ] The breach decision used the unrounded figure, and at-limit is understood to breach.

## Gate and escalation

- [ ] Threshold breach blocks new position entries.
- [ ] Breach and coverage warnings are captured by the logging pipeline, not just returned.
- [ ] Limitations are understood by whoever consumes the number: single-period revaluation,
      no correlation model, no margin call or forced liquidation, linear in position value,
      not valid for options without Greeks, not a regulatory calculation.

## Verification

- [ ] Run `python -m unittest discover -s skills/stress-testing-against-historical-crash-scenarios/scripts`
      — 100% pass rate.
- [ ] Run `python tools/validate_skills.py` — no errors for this skill.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested: ___________________________
- Scenario library source & as-of date: ___________________________
