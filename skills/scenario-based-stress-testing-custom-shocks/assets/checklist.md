# Pre-Flight Checklist

## Position mapping
- [ ] Is there one row per **factor exposure**, not per instrument (a convertible carries an equity row and a credit row)?
- [ ] Is `current_value_usd` **signed**, with shorts negative?
- [ ] For `YIELD_BPS` factors, is `beta_to_factor` a **positive** duration in years (the engine applies the minus sign)?
- [ ] For `RELATIVE_RETURN` factors, is `beta_to_factor` a return elasticity — and was it estimated on data that includes a stress regime, not just a calm one?

## Scenario definition
- [ ] Is each factor's shock type chosen from the factor's nature (price-like → relative, yield-like → bps) rather than copied from a neighbouring scenario?
- [ ] Are basis-point magnitudes written as basis points (`300.0`), never as `3.00`?
- [ ] Are scenario ids unique across predefined and custom scenarios?
- [ ] Are the default magnitudes recalibrated, or has the decision to keep them been recorded? (−35% equity is far milder than the −56.8% 2007–2009 decline.)
- [ ] Is at least one scenario forward-looking rather than a historical replay?

## Capital base and limits
- [ ] Is `capital_base_usd` stated explicitly whenever the book contains a short?
- [ ] Is the base the capital the limit is actually set against — not gross exposure, not net exposure?
- [ ] Is `max_allowed_drawdown_pct` calibrated and documented as a house limit, not left at the library default?

## Reading the report
- [ ] Was `report.status` checked before any P&L number was quoted?
- [ ] Were `factors_never_shocked` and per-scenario `unshocked_asset_ids` reviewed — is every $0 result explained by "nothing moved" rather than "nothing matched"?
- [ ] Are the breaches in `breached_scenario_ids` escalated, and is the worst-case scenario logged for risk committee review?
- [ ] Is a logging handler configured so breach and coverage WARNINGs are not discarded?

## Known limits of the answer
- [ ] Has the book been checked for options positions whose convexity this linear model does not price?
- [ ] Is any instrument that can trade through zero (e.g. a physically-settled crude contract) handled outside the relative-shock model?
- [ ] Has the liquidation cost and the time to get out been assessed separately, rather than assumed away by a mark at shocked prices?
- [ ] Are the scenario definitions archived alongside the report, so the numbers remain interpretable later?
