---
name: position-limit-reporting-cftc-large-trader
description: >-
  Use when a US futures and options portfolio must be screened against CFTC
  large-trader reporting levels (17 CFR 15.03) and Part 150 federal speculative
  position limits, aggregating sub-account holdings per legal entity and keeping
  the reporting test (gross, per contract month, "equals or exceeds") strictly
  separate from the limit test (net, per limit type, "in excess of").
domain: Regulatory Compliance & Risk Controls
subdomain: CFTC Regulatory Reporting & Speculative Position Limits
tags: ["cftc", "form-102", "large-trader-reporting", "position-limits", "futures", "speculative-limits", "regulatory-reporting"]
brokers_frameworks: ["CFTC 17 CFR Part 15 (reporting levels)", "CFTC 17 CFR Part 17 (Form 102 / OCR)", "CFTC 17 CFR Part 150 (speculative position limits)", "Python Standard Library"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill for **buy-side self-surveillance** of US futures and options positions: knowing, before your carrying broker tells the Commission, that your aggregated position has reached a large-trader reporting level, and knowing continuously whether it is over a federal speculative position limit.

Two distinct regimes are in play and they must not be collapsed into one number:

- **Reporting levels — 17 CFR 15.03.** A "reportable position" is one that, at the close of the market on any business day, "equals or exceeds" the level in the §15.03(b) table in either "any one future of any commodity on any one reporting market" or in the options exercising into that same future (§15.00(p)(1)). An account holding one is a "special account" (§15.00(r)).
- **Speculative limits — 17 CFR 150.2.** No person may hold or control positions "net long or net short, in excess of" the Commission's levels, tested separately for the spot month, a single month, and all months combined.

The tests use different arithmetic (gross-per-side vs net), different buckets (per contract month vs per limit type), and different boundary operators (`>=` vs `>`). Conflating them is the defining failure mode of home-grown position-limit code.

## When NOT to Use

- **As a filing tool.** This skill files nothing and produces no submission payload. Form 102A is filed by the *carrying firm*, not by you: §17.01(a) puts the obligation on "the futures commission merchant, clearing member, or foreign broker". Your entity's own CFTC form is **Form 40**, filed "after a special call upon such trader by the Commission or its designee" (§18.04(a)) — not on a routine daily schedule. If you believe you have a daily filing obligation as a trader, re-read §17.01 before building anything.
- **As the sole determinant of who aggregates with whom.** §150.4(a)(1) requires aggregating accounts where a person "directly or indirectly controls trading or holds a 10 percent or greater ownership or equity interest", subject to eight exemption categories in §150.4(b) (independent account controllers, independently operated owned entities, limited-partner interests, and others). That is a legal determination. Feed this engine the post-aggregation set; it enforces internal consistency, not entitlement.
- **As a bona fide hedge adjudicator.** `is_bona_fide_hedge` is an input you assert, not a conclusion the engine reaches. §150.3 exempts qualifying hedges from limits; qualifying is a legal question.
- **For swaps.** Part 20 large swaps trader reporting is out of scope. Its routine position reports were **sunset by the Commission effective 21 July 2026** (Release 9269-26); only recordkeeping and special-call provisions survive. Economically equivalent swaps still count toward Part 150 limits and must be converted and supplied by you.
- **For exchange-set limits and accountability levels.** Outside the spot month, non-legacy contracts are governed by DCM limits or accountability levels, not federal limits. This engine tests what you configure and explicitly reports what it did *not* test.
- **As a substitute for futures-equivalent conversion.** The engine performs no delta conversion. Option positions must already be on a futures-equivalent basis for the limit tests to mean anything.

## Prerequisites

- Post-aggregation account positions per §150.4, each carrying `account_id`, `entity_name`, `commodity_code`, `contract_month`, `long_position`, `short_position`, `instrument_class` (`FUTURE` or `OPTION`), and `is_bona_fide_hedge`.
- **Gross legs, not a net.** Net is derived. A caller-supplied net that disagrees with its own legs is precisely the input that hides a reportable position.
- `contract_month` spelled consistently across accounts — it is compared as an opaque string, so `'2026-12'` and `'DEC26'` will not aggregate together.
- A `CFTCLimitSpec` resolved **at the evaluation date**: the reporting level from the §15.03(b) table, and whichever of `spot_month_limit`, `single_month_limit`, `all_months_combined_limit` actually exist for that contract. Nothing is hard-coded — see `references/standards.md` for where the authoritative tables live.
- The `spot_month` label, whenever a `spot_month_limit` is configured.

## Workflow

1. **Aggregate per legal entity, with integrity checks**:
   - Fold every account's legs into buckets keyed by `(contract_month, instrument_class)`.
   - **Decision point — a foreign entity, a mismatched commodity, or a duplicated account bucket raises, it does not aggregate.** Summing whatever list arrives is how an upstream query bug becomes a missed filing. The same `(account_id, contract_month, instrument_class)` supplied twice is double counting; consolidate it upstream.
   - Positions flagged bona fide hedge stay in the aggregate for reporting and are withheld only from the limit tests.

2. **Reporting-level audit (§15.00(p)(1), §15.03)**:
   - Per bucket, test **each side separately**: reportable if `gross_long >= level` **or** `gross_short >= level`.
   - **Decision point — never sum the sides and never net them.** 200 long against 200 short is not 400 against a 350 level; neither side reaches 350 and nothing is reportable. Conversely 400 long against 300 short nets to 100 but the long side is squarely reportable. The summing error over-reports; the netting error hides a special account.
   - **Decision point — never pool contract months.** §15.00(p)(1)(i) says "any one future". 200 in December plus 200 in January is not a reportable 400.
   - **Decision point — options are their own bucket.** §15.00(p)(1)(ii) tests options exercising into the same future separately from the future itself.
   - The boundary is inclusive: exactly at the level *is* reportable.

3. **Federal speculative limit audit (§150.2)**:
   - Net long against short (non-hedge positions only), then test each configured limit: spot month against the spot-month bucket, single month against every month individually, all-months-combined against the total.
   - **Decision point — the boundary is exclusive.** "In excess of" means exactly at the limit is not a breach. This is deliberately one contract different from step 2.
   - **Decision point — a limit that does not exist is not tested, and the report says so.** Only the nine legacy agricultural contracts carry federal single-month and all-months-combined limits; the other sixteen core referenced futures contracts carry a federal spot-month limit only. Modelling one scalar limit per commodity manufactures breaches outside the spot month for crude oil and metals. A `None` limit yields an entry in `limits_not_tested` — absence of a breach there is not evidence of compliance.
   - **Decision point — a configured spot-month limit with no `spot_month` supplied raises.** Silently skipping a control you configured is worse than refusing to run.

4. **Report**: emit `CFTCLargeTraderReport` with independent `is_reportable` and `is_limit_breached` flags, per-bucket `month_detail`, itemised `breaches`, and `limits_not_tested`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Believing you file Form 102A.** You do not. §17.01(a) assigns it to the FCM, clearing member, or foreign broker. Building a daily trader-side "Form 102A filing pipeline" implements an obligation that does not exist while leaving the one that does — Form 40 on special call, §18.04(a) — unbuilt.
- **One scalar "the CFTC limit" per commodity.** For NYMEX crude oil there is no federal all-months-combined limit; comparing an all-months net against an invented one raises false violations, and the real spot-month constraint goes untested. Some spot-month limits also step down *within* the spot month (the CFTC's published Live Cattle limit steps 600 → 300 → 200), so even the spot-month number is not constant across the month.
- **Summing long and short for the reporting test.** `long + short` is not a quantity §15.00(p) defines. A market-neutral book of 200 long / 200 short is flagged at a 350 level that neither side approaches.
- **Netting for the reporting test.** The mirror-image error, and the dangerous one: a large gross long offset by a large gross short reports as a small net and the special account is never identified.
- **Pooling contract months, or pooling futures with options.** Both inflate a single bucket past a level that no individual bucket reaches.
- **Confusing the two boundary operators.** Reporting is "equals or exceeds"; limits are "in excess of". A position exactly at the number is reportable but not a breach.
- **Failing to aggregate sister funds.** Evaluating sub-accounts separately misses the entity-level position that §150.4 says is the one that counts.
- **Treating the reporting flag as an intraday control.** §15.00(p) is a close-of-market test. §150.2 prohibits *holding or controlling* an excess position and is not limited to the close — run the limit audit intraday, and read the reporting flag only off an end-of-day snapshot.
- **Reading `status` instead of the flags.** `status` collapses to `SPECULATIVE_LIMIT_BREACHED` when a position is both breached and reportable. The flags are independent; read them.
- **Treating a disabled-engine report as a clean bill.** `ENGINE_DISABLED` asserts nothing about the positions.
- **Hard-coding levels.** The §15.03(b) table and Appendix E to Part 150 are amended over time. Resolve them at the evaluation date and archive what you used, or the audit is not reproducible.

## Verification

- Instantiate `PositionLimitReportingCFTCLargeTraderEngine`. Aggregate two sub-accounts of `ACME_FUND` holding 200 contracts long each in the same crude oil contract month against a 350-contract reporting level ⟹ `is_reportable = True`, `reportable_buckets = ('2026-12/FUTURE',)`, `reportable_side = 'LONG'`.
- Change the second account to 200 **short** ⟹ `is_reportable = False`. Neither side reaches 350; the sides are never summed.
- Set 400 long against 300 short ⟹ net is 100 but `is_reportable = True` on the long side.
- Split 200 long across December and January ⟹ `is_reportable = False`, two entries in `month_detail`.
- Boundary checks: exactly 350 long is reportable; 349 is not. Exactly 6,000 net against a 6,000 spot-month limit is **not** a breach; 6,001 is, with `excess = 1.0`.
- Configure a crude-oil-shaped spec (spot-month limit only) and hold 20,000 contracts in a deferred month ⟹ `is_limit_breached = False` and `limits_not_tested = ('SINGLE_MONTH', 'ALL_MONTHS_COMBINED')`.
- Negative checks: a position belonging to another entity, a mismatched `commodity_code`, a duplicated `(account_id, contract_month, instrument_class)`, a negative or non-finite leg, an unknown `instrument_class`, and a configured `spot_month_limit` with no `spot_month` must each raise `ValueError`.
- Run `python -m unittest discover -s skills/position-limit-reporting-cftc-large-trader/scripts` — 38 tests, 100% pass rate.

## Related Skills

- `position-limit-breach-simulation-fire-drills`
- `leverage-limit-enforcement-across-instruments`
- `concentration-risk-single-name-limits`
- `cftc-commodity-pool-operator-registration`
- `futures-contract-roll-automation`
