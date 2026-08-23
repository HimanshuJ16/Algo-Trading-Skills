# Pre-Flight Checklist

- [ ] Does the configured `priority_list` cover every currency in your traded universe? (The default covers only the eight majors; anything else is reported `UNCLASSIFIED`.)
- [ ] Is the team aware that the ranking is a **de-facto market convention**, not an ISO 4217 rule, and has it been confirmed against each vendor's symbology?
- [ ] Are inverted quotes cross-inverted ($\text{Bid}_{\text{std}} = 1/\text{Ask}_{\text{inv}}$) rather than same-side inverted?
- [ ] Is the pipeline gated on `classification`, not on `is_inverted == False`? (The latter also covers pairs the module could not rank.)
- [ ] Are `UNCLASSIFIED` quotes — metals (`XAU/USD`), crypto, exotics — routed to review instead of straight into pricing, and confirmed to be passed through **unchanged**?
- [ ] Are non-finite, zero, and negative prices rejected on the standard path as well as the inversion path?
- [ ] Is pip size taken from the **normalized** terms currency, so an inverted `JPY/USD` feed gets `0.01` and not `0.0001`?
- [ ] Are terms currencies quoted to two decimals beyond `JPY`, and any non-FX pairs, configured via `two_decimal_terms_currencies` / `pip_size_overrides` rather than inheriting the `0.0001` default?
- [ ] Is `is_crossed` checked before a spread reaches a transaction-cost model? (A negative spread understates cost.)
- [ ] Does anything downstream re-round the published prices, and if so, is `spread_pips` recomputed from the rounded values so the two stay consistent?
