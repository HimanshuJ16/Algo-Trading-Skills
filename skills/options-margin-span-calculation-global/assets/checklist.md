# Pre-Flight / Sign-off Checklist — options-margin-span-calculation-global

Use this before considering the skill's implementation complete.

## Regime and parameters

- [ ] **Margin regime identified:** exchange SPAN, CME SPAN 2, OCC TIMS portfolio margin, NSE SPAN + ELM, or Reg-T strategy-based. (SPAN 2 and TIMS are different models — a scenario scan does not approximate them.)
- [ ] **Parameters sourced from the exchange parameter file**, not from library defaults — price scan range, volatility scan range, short option minimum, extreme-move multiplier and cover fraction. Defaults in `span_approx.py` are placeholders.
- [ ] **Scan range matches the product class.** An index-scale range (±6%) on a single-name equity option understates the requirement; TIMS stresses individual equities at ±15%.
- [ ] **`short_option_minimum_per_contract` is set** whenever the portfolio holds short options. At 0.0 a far-OTM short book scans to nearly nothing (the calculator logs a warning).
- [ ] **`exposure_margin_pct` reflects the venue** — an NSE-style ELM overlay, or `0.0` for CME-style performance bonds.

## Inputs

- [ ] **Every leg carries `time_to_expiry_years`.** Without it the leg is valued at intrinsic, the volatility scan cannot reach it, and short-leg requirements are understated. Check `result.valuation_mode` is `black_scholes`.
- [ ] **Marks are current**, supplied via `mark_price` where available. `premium` is the entry price and is deliberately not used in the calculation.
- [ ] **`multiplier` read from the contract specification**, not assumed to be 100 (adjusted contracts after a split or special dividend differ).
- [ ] **All legs share one underlying and, ideally, one expiry.** Calendar and inter-commodity spread charges are not modelled.

## Behaviour

- [ ] **16-scenario grid:** `evaluate_scenario_grid()` returns 16 entries — 14 price×volatility pairings plus 2 fractionally-covered extreme moves.
- [ ] **Long-only positions return a zero requirement.** A long option is paid for in full.
- [ ] **Defined-risk capping:** a bounded-loss spread's requirement never exceeds `worst_case_terminal_loss` (`margin_capped_at_max_loss` shows when the cap bound).
- [ ] **Unbalanced structures get no relief:** a ratio or unmatched spread is margined against its real exposure, not treated as hedged because an opposite-signed leg exists.
- [ ] **`has_unbounded_loss` reviewed, not just the headline number.** True means net short calls and no finite cap.
- [ ] **Volatility sensitivity confirmed:** the requirement changes when implied volatility changes. If it does not, the volatility axis is not reaching the valuation.

## Operations

- [ ] **Reconciled against the broker's calculator** on a sample of representative positions, with the bias recorded and its sign noted. A persistently low bias is a blocker.
- [ ] **Intraday margin monitored** as a live figure and routed to the utilisation circuit breaker, not just logged.
- [ ] **Backtest margin utilisation modelled** if the strategy could ever be capital-constrained.
- [ ] **Automated testing:** run `python -m unittest discover -s skills/options-margin-span-calculation-global/scripts` and confirm a 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
