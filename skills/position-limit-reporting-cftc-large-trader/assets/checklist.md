# Pre-Flight / Sign-off Checklist — position-limit-reporting-cftc-large-trader

US / CFTC only. Use before relying on a position-limit audit run.

## Scope

- [ ] **Filing obligation understood:** Form 102A is filed by the FCM, clearing member or foreign broker (§ 17.01(a)), *not* by this entity. No trader-side "daily Form 102A pipeline" has been built.
- [ ] **Form 40 path exists:** the entity can respond to a § 18.04(a) special call. That is the trader-side obligation.
- [ ] **Swaps handled elsewhere:** Part 20 routine position reports were sunset effective 21 July 2026; economically equivalent swaps still count toward Part 150 limits and are converted and supplied separately, or their absence is documented.
- [ ] **Exchange limits handled elsewhere:** non-spot exposure in non-legacy contracts is governed by DCM limits/accountability levels, which this audit does not cover.

## Levels

- [ ] **Reporting level resolved at the evaluation date** from the § 15.03(b) table — not from a cached constant or a doc example.
- [ ] **Only the limits that exist are configured.** Non-legacy contracts get `spot_month_limit` only; `single_month_limit` and `all_months_combined_limit` are left `None`.
- [ ] **No `None` limit approximated by a large number.** `None` means not tested; a large sentinel silently certifies compliance.
- [ ] **Today's spot-month level used.** Some spot-month limits step down within the spot month (Live Cattle: 600 → 300 → 200). The level was re-resolved for this business day.
- [ ] **`spot_month` supplied** wherever `spot_month_limit` is set.

## Inputs

- [ ] **Aggregation set built per § 150.4:** all accounts under direct/indirect trading control or 10%+ ownership, with any § 150.4(b) exemption relied upon documented (and its notice filing made where required).
- [ ] **One record per `(account_id, contract_month, instrument_class)`** — multi-row sources consolidated upstream, not left for the engine to add.
- [ ] **Gross legs, both non-negative.** No signed shorts, no caller-supplied net.
- [ ] **`contract_month` spelled consistently** across every account and source system.
- [ ] **Option positions for limit purposes already on a futures-equivalent basis** — the engine does no delta conversion.
- [ ] **`is_bona_fide_hedge` set only where the § 150.3 claim is defensible**, and the supporting documentation exists.
- [ ] **Clean run is the evidence:** the engine raises on foreign entities, mismatched commodities, duplicate buckets, and negative/non-finite legs.

## Arithmetic

- [ ] **Reporting test is gross, per bucket, per side, `>=`.** Sides not summed, sides not netted, months not pooled, options not pooled with futures.
- [ ] **Limit test is net, per limit type, strict `>`.** Exactly at the limit is not a breach.
- [ ] **The two boundaries are known to differ by one contract**, and no downstream consumer has re-implemented either.

## Timing

- [ ] **Limit audit runs intraday** — § 150.2 prohibits holding or controlling an excess position at any time.
- [ ] **Reporting flag read only off an end-of-day snapshot** — § 15.00(p) is a close-of-market test, and `as_of` records which close.

## Reading the result

- [ ] **`is_reportable` and `is_limit_breached` read as independent flags**, not inferred from `status`.
- [ ] **`limits_not_tested` reviewed.** A clean report on a spot-month-only spec is not "within federal limits".
- [ ] **`ENGINE_DISABLED` never treated as a pass.**
- [ ] **`hedge_exempt_contracts_excluded` reviewed** — that exposure was withheld from the limit tests on your own assertion.
- [ ] **Breach escalated as a compliance event**, not logged as a metric.

## Reproducibility

- [ ] **Levels, `spot_month`, `as_of`, and the aggregation set archived with the report.** Threshold-dependent audits are not reproducible without them.

## Automated Testing

- [ ] Run `python -m unittest test_position_limit_reporting_cftc_large_trader` from the `scripts/` directory — 38 tests, 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Levels used (reporting / spot / single / all-months): ___________________________
