# Deep Workflow Reference — options-margin-span-calculation-global

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Select the margin regime.**
   - Exchange SPAN (legacy scenario grid), CME SPAN 2 (filtered historical simulation
     VaR), OCC TIMS portfolio margin under FINRA Rule 4210(g), NSE Clearing SPAN plus
     Extreme Loss Margin, or Reg-T strategy-based margin. These are different models,
     not different calibrations of one model.
   - `SPANMarginCalculator` implements the legacy scenario-grid family only.

2. **Load the exchange parameter file.**
   - Price scan range, volatility scan range, short option minimum, extreme-move
     multiplier and cover fraction are daily exchange-published inputs. The library
     defaults are placeholders retained for backward compatibility and are not the
     values for any particular contract.
   - Reference stress ranges when a SPAN parameter file is unavailable and a portfolio-
     margin-style range is the closer analogue: −8%/+6% high-capitalisation broad-based
     index, ±10% non-high-capitalisation broad-based index, ±15% sector index and
     individual equity (OCC Customer Portfolio Margin / TIMS).

3. **Scenario grid evaluation — the 16 standard SPAN risk-array scenarios.**
   - `SPANMarginCalculator.evaluate_scenario_grid()` builds scenarios 1–14 as the seven
     price fractions {0, ±1/3, ±2/3, ±1} of the price scan range, each paired with the
     volatility scan range up and down, plus scenarios 15–16 at ±(extreme multiplier ×
     price scan range) with volatility unchanged and only a fraction of the loss covered.
   - Each entry is the change in portfolio value versus the current model value, with the
     extreme scenarios already scaled by the cover fraction — so `min()` over the returned
     mapping is the scan risk directly.
   - Legs are revalued with European Black-Scholes at the scenario spot and scenario
     volatility. A leg with no `time_to_expiry_years` is valued at intrinsic instead,
     which silently removes the volatility axis for that leg; the calculator logs a
     warning and reports `valuation_mode`.

4. **Combine into the requirement.**
   - `span_scan_risk = max(0, −min(scenario values))`.
   - `net_option_value = Σ quantity × multiplier × mark` (positive net long, negative net
     short), taken from `mark_price` where supplied and from the model otherwise.
   - `risk_requirement = max(span_scan_risk, short_option_minimum)`, following CME's rule
     that the SPAN methodology compares the computed value with the short option minimum
     and takes the larger.
   - `total = max(0, risk_requirement − net_option_value) + exposure_margin`, following
     CME's "total SPAN margin = SPAN risk requirement less the net option value". A
     long-only position therefore nets to zero, which is correct: long options are paid
     for in full.

5. **Bounded-loss test and defined-risk cap.**
   - The expiry payoff is piecewise linear with kinks only at the strikes, so its minimum
     over [0, ∞) is attained at zero, at a strike, or in the limit. `_worst_case_terminal_loss()`
     evaluates all of them.
   - The loss is unbounded exactly when the multiplier-weighted net call quantity is
     negative. Puts cannot run away because the underlying is floored at zero — so a naked
     short put reports as bounded, with a very large bound. Read the loss figure, not the
     `is_defined_risk` flag alone.
   - Where the loss is bounded, the requirement is capped at it and
     `margin_capped_at_max_loss` is set. This is what stops a defined-risk spread being
     charged more than it can possibly lose.
   - Bounded-ness is derived from the payoff, never from leg shape. Ten short puts against
     one long put, and a 1×2 ratio call spread, both pair up by option type and neither is
     defined-risk.

6. **Exposure / Extreme Loss Margin overlay.**
   - `exposure_margin_pct` applies a flat percentage of **short-leg** notional on top of
     SPAN, modelling NSE-style ELM. Long legs attract none.
   - It has no analogue in a CME performance bond; set it to `0.0` outside that context.
   - NSE Clearing's published base ELM rates are 2% for index futures and 3.5% for stock
     futures, with product- and event-specific overlays. The library default of 3% is a
     placeholder retained for backward compatibility.

7. **Intraday recalculation monitoring.**
   - Re-run `calculate_span_margin()` on live marks and live implied volatility. A
     volatility spike raises the requirement on an unchanged position.
   - Route the utilisation figure to the circuit breaker rather than to a log line; see
     `margin-utilization-circuit-breaker`.

8. **Reconciliation.**
   - Compare a sample of estimates against broker figures, record the bias with its sign,
     and re-check after any parameter-file change. Treat a persistently low bias as a
     blocker.

## Failure Modes Observed in Production

- **Naive leg summation.** Charging each short leg its naked requirement on a defined-risk
  spread, over-constraining capital by a multiple of what the position actually blocks.
- **Shape-matched defined-risk relief.** Granting spread treatment because an opposite-
  signed leg of the same option type exists, without checking quantities or the payoff —
  which margins nine naked puts at zero.
- **Margining long options.** Charging premium already paid as if it were collateral.
- **A dead volatility axis.** A scan whose valuation ignores the scenario volatility, so
  half the matrix is duplicated and vol shocks never move the requirement.
- **Entry-premium anchoring.** Measuring scenario profit and loss from the fill price
  rather than the current mark, making margin a function of trade history.
- **Borrowed scan ranges.** Index-scale price scan ranges applied to single-name equity
  options.
- **Missing short option minimum.** Far-OTM short books scanning to a near-zero requirement.
- **Static entry margin assumptions.** Assuming the requirement is fixed for the life of
  the trade, ignoring intraday recalculation.
- **Unmodelled capital constraints in backtests.** Assuming infinite capital by ignoring
  margin utilisation bounds.
- **Cross-venue methodology conflation.** Treating requirements as identical across venues
  with different models, or assuming a CME product is still on legacy SPAN after its SPAN 2
  migration.

## Known Limitations of the Reference Implementation

- Single underlying only. Intra-commodity (calendar) and inter-commodity spread charges and
  credits, delivery/spot charges and cross-margin offsets are not modelled, so a multi-expiry
  or multi-underlying portfolio is mis-margined.
- Option legs only. A delta hedge held in the underlying or in futures is invisible to both
  the scan and the bounded-loss test.
- European valuation. Early exercise, assignment and pin risk are not priced; see
  `early-exercise-assignment-risk-management`.
- No dividends or cost of carry in the Black-Scholes valuation.
- A single implied volatility per scan — no smile, skew or term structure; see
  `options-implied-volatility-surface-construction`.
- Not CME SPAN 2, not OCC TIMS, not Reg-T strategy-based margin.

## Production Implementation Reference

- Reference code: `scripts/span_approx.py` (`SPANMarginCalculator`, `OptionLeg`, `OptionType`,
  `SPANMarginResult`).
- Automated unit tests: `scripts/test_span_approx.py`.
