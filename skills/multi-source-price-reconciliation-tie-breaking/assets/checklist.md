# Pre-Flight / Sign-off Checklist — multi-source-price-reconciliation-tie-breaking

Use this before considering the skill's implementation complete.

## Sources and inputs

- [ ] **Three or more sources:** Confirm at least three vendors quote the symbol. With two, no outlier is attributable and the filter is skipped by design.
- [ ] **Genuine independence:** Confirm no two configured vendors resell the same underlying feed. Two badges on one source agree with each other while being wrong together.
- [ ] **Same price basis:** Confirm every vendor publishes the same field (all last trade, or all quote midpoint) in the same currency for every reconciled symbol.
- [ ] **Single receipt clock:** Confirm `timestamp` and `as_of` come from one local clock, and that no vendor or exchange event timestamp is used.
- [ ] **Entitlements:** Confirm each vendor feed is licensed for the intended use, including any redistribution of the canonical price.

## Validation

- [ ] **Non-finite prices:** Confirm `NaN` and `±inf` raise at quote construction, not at the outlier comparison.
- [ ] **Non-positive prices:** Confirm zero and negative prices raise, and that no instrument in the universe can legitimately price at or below zero.
- [ ] **Weights:** Confirm every `reliability_weight` is strictly positive.
- [ ] **Symbol identity:** Confirm a quote for a different instrument raises rather than being priced.
- [ ] **Duplicate vendors:** Confirm a repeated `vendor_id` raises. Check the dispatcher for replay and double-subscription paths that could produce one.

## Calibration

- [ ] **Tick floor set:** Confirm `min_absolute_tolerance` equals the instrument's minimum price increment. (5 bps is narrower than one $0.01 tick for any NMS stock between $1.00 and $20.00.)
- [ ] **Calibrated, not defaulted:** Confirm `tolerance_pct` and `max_deviation_pct` were derived from recorded cross-vendor history for this vendor set, not copied from the reference defaults.
- [ ] **Bound sanity:** Confirm no effective bound reaches 100% of the price — a tick floor set in the wrong price units silently disables the check it governs. The engine logs a warning when this happens; make sure that warning is not being filtered out.
- [ ] **No boundary calibration:** Confirm no alert or limit depends on exact equality at a threshold — decimal thresholds are not exactly representable in binary floating point.
- [ ] **Precision set:** Confirm `price_precision` matches venue metadata, or is left `None`. Confirm no sub-cent instrument is rounded to zero.
- [ ] **Staleness threshold:** Confirm `max_quote_age_seconds` is enabled and exceeds the instrument's own quiet periods, so an illiquid symbol is not permanently stale between genuine ticks.

## Behaviour under failure

- [ ] **Stale exclusion:** Confirm a frozen vendor *inside* the deviation bound is removed by the staleness gate, not left for the outlier filter that cannot see it.
- [ ] **Explicit clock:** Confirm `as_of` is a real clock reading and is never derived from the freshest quote in the batch.
- [ ] **Total blackout:** Confirm that when every quote is stale the result carries `canonical_price is None`, and that no caller substitutes a last known value.
- [ ] **Deadlock:** Feed a bimodal even-count batch (100, 100, 105, 105) and confirm `filter_deadlocked is True` with an unresolved, unverified status — not "4 valid, 0 outliers, success".
- [ ] **Single survivor:** Confirm a batch reduced to one surviving quote reports `RECONCILIATION_UNCORROBORATED`, not success.
- [ ] **Fast market:** Replay a real gap event (earnings, halt resumption) and confirm the deviation bound is wide enough that a leading vendor is not attributed as an outlier.

## Determinism and audit

- [ ] **Order independence:** Rotate the quote list and confirm both the tie-break winner and the composite price are bit-identical across every rotation.
- [ ] **Config typos:** Confirm an unrecognised `tie_breaker_method` raises rather than falling back to list order.
- [ ] **Partition invariant:** Confirm `valid + outliers + stale == total` on every report.
- [ ] **Policy labelled as policy:** Confirm dashboards and reviewers see a tie-broken price as an operator preference, not as a detection of which vendor was wrong.
- [ ] **Retention:** Confirm reports are retained long enough to reconstruct any disputed mark, and that they record the effective tolerance actually applied.

## Downstream integration

- [ ] **Trust flag wired:** Confirm `is_cross_verified is False` actually gates sizing, order entry or valuation somewhere downstream — the engine itself stops nothing.
- [ ] **`None` handled:** Confirm every consumer of `canonical_price` handles `None` explicitly rather than propagating it into arithmetic.
- [ ] **Status branching:** Confirm consumers branch on `status` rather than truth-testing it as a string.
- [ ] **Concurrency:** Confirm the engine's `config` is not mutated after construction if the instance is shared across threads.
- [ ] **Independent price verification:** If the firm is CRR-scope, confirm this engine's output feeds the periodic IPV process (Article 105(8)) rather than being assumed to satisfy it.

## Testing

- [ ] **Automated Testing:** Run `python -m unittest discover -s skills/multi-source-price-reconciliation-tie-breaking/scripts` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
