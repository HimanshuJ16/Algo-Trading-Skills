---
name: sanctions-screening-for-counterparties-and-instruments
description: >-
  Use when onboarding a counterparty or admitting an instrument, to screen identifiers,
  primary names and published aliases with Unicode normalisation before edit-distance
  matching, and to leave evidence rather than a boolean. Supply your own list data.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: regulatory-compliance-global
  tags: sanctions-screening, ofac-sdn, ofac-ssi, eu-consolidated, un-sanctions, uk-sanctions-list, fuzzy-matching, ofac-50-percent-rule, embargo
  brokers_frameworks: "OFAC SDN List; OFAC Sectoral Sanctions Identifications (SSI) List; OFAC 50 Percent Rule; EU Consolidated Financial Sanctions List; UN Security Council Consolidated List; UK Sanctions List (FCDO); Wolfsberg Sanctions Screening Guidance 2019; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when onboarding a trading counterparty (prime broker, execution venue, liquidity provider, OTC desk, clearing member) or admitting a new instrument to a tradable universe, and you need the screening decision to leave evidence rather than a boolean. It turns a subject record plus a dated list snapshot into a reproducible status with a per-hit finding trail: what matched, on which list, by which method, at what score, against a list pulled on what date.

Reach for it specifically because sanctions screening fails *silently*. A screen that never ran, ran against a stale list, or compared a name in the wrong namespace produces exactly the same artefact as a clean screen — a report saying `CLEARED` — and no alert will ever fire about it. Every design decision in this engine is aimed at that: it would rather raise than clear.

## When NOT to Use

- **As a sanctions list feed.** This ships no data. `DEMO_SANCTIONED_DATABASE` is five hand-written rows for the tests; screening real counterparties against it produces a clean report and no coverage at all. Supply your own snapshot from OFAC, the EU FSF service, the UN, or the UK Sanctions List — or from a vendor.
- **As a commercial matching engine.** The matching here is normalisation plus edit distance plus sorted-token distance. It does **not** do phonetic matching (Soundex/Metaphone), cross-script transliteration (Cyrillic/Arabic/Han → Latin), or nickname expansion. A name arriving in Cyrillic will not match a Latin list entry. If your risk assessment demands those, you need a vendor; this is a control you can read and test, not a replacement for one.
- **As a legal determination.** A hit is an input to a compliance officer's decision. Whether a specific dealing is licensed, generally authorised, or prohibited is a conclusion for counsel and your MLRO.
- **As your firm's sanctions compliance programme.** OFAC's 2019 Framework describes five components — management commitment, risk assessment, internal controls, testing and auditing, and training. This engine is one internal control inside such a programme.
- **For beneficial-ownership discovery.** It evaluates ownership percentages you supply; it does not resolve a cap table. See `kyc-aml-considerations-for-algo-trading-entities`.

## Prerequisites

- **A dated list snapshot.** `SanctionsListSnapshot` requires an `as_of` date and rejects an empty entry list. Both are deliberate: an undated list cannot be checked for staleness, and an empty list — the shape a failed feed load takes — screens every subject as clean.
- **Country identifiers resolvable to ISO 3166-1 alpha-2.** Anything unresolvable, blank, or an `XX`/`ZZ`-style "unknown" placeholder raises rather than screening.
- **ISO 3166-2 subdivision codes where you have them** (`region_code`). Required in practice for any Ukraine exposure — see the workflow.
- **Ownership aggregated per blocked person**, itemised via `sanctioned_owners` in preference to a single pre-aggregated float.
- **A calibrated fuzzy threshold.** The 85.0 default is an engineering starting point with no regulatory basis. Calibrate it against your own list and book, and record the calibration.

## Workflow

1. **Supply the list explicitly and date it.** The constructor takes a `SanctionsListSnapshot` and has no default. The previous version accepted `sanctions_database=None` and fell back to demo rows, so a caller whose feed load returned `[]` screened real counterparties against five fixtures and got `CLEARED`. There is now no code path that screens against a list you did not pass.
2. **Normalise the jurisdiction before comparing it, and fail closed.** `normalize_country` resolves alpha-2, alpha-3 and common names to alpha-2 and raises on anything else. The failure that actually happens is not a typo but a namespace change: an upstream feed switching to full country names screens `"IRAN"` against a set holding `"IR"`, clears Iran on every screen, and looks healthy doing it.
3. **Resolve the subdivision before trusting a Ukraine clear.** The Crimea and DNR/LNR embargoes are *territorial*. Every affected entity reports country `UA`, so a country-code screen cannot see them. Supply `region_code` (`UA-43`, `UA-40`, `UA-14`, `UA-09`); if you do not, the engine returns `REVIEW_REQUIRED` with a `NO_REGION_SUPPLIED` advisory rather than a clear it cannot justify.
4. **Normalise names before measuring distance, not after.** `"VTB BANK P.J.S.C."` against a list holding `"VTB BANK PJSC"` is a 76.47% edit similarity on the raw strings — below any sane threshold, and therefore a clean pass for a designated bank. After punctuation and accent folding the two are identical. Normalisation is what makes the threshold meaningful; raising the threshold instead would only make it worse.
5. **Screen the aliases, not just the primary name.** Designated entities are published under multiple a.k.a. names, and the name a counterparty gives you is frequently the a.k.a. OFAC's 2019 Framework names failure to "account for alternative spellings" as a root cause of real violations. `SanctionedEntry.aliases` is screened alongside `name`, and a hit reports both.
6. **Aggregate the 50 Percent Rule across blocked owners.** OFAC aggregates: two blocked persons at 25% each block the entity. A caller who compares each holder to 50% individually never triggers the rule at all. Supply `sanctioned_owners` and let the engine aggregate.
7. **Classify the 50% hit differently from a list hit.** At or above 50% aggregate blocked ownership the entity *is* blocked property whether or not it appears on the SDN List — block and report to OFAC, do not merely decline. That is why `BLOCKED_OFAC_50_PERCENT_RULE` outranks a list hit in the status precedence and exposes `report.requires_ofac_blocking_report`.
8. **Do not collapse sectoral into blocking.** A sectoral (SSI) designation restricts *defined transaction types* with an otherwise tradable entity; a blocking designation freezes property. Treating SSI as blocking over-blocks lawful business, and treating blocking as SSI is a violation. The engine returns `RESTRICTED_SECTORAL` and never merges the two.
9. **Read the advisories, not just the status.** A stale list or an unresolved embargoed-territory country returns `REVIEW_REQUIRED` — not a hit, but not a clear either: a screen whose *negative* result cannot be relied on. `report.hits` always carries every finding, so a block never conceals a second problem.
10. **Pass `screened_on` explicitly.** It defaults to today for convenience, but an audit record that cannot be reproduced is not an audit record.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Screening against a list you did not check the age of.** OFAC's Framework names failure "to update their screening software to incorporate updates to the SDN List or the Sectoral Sanctions Identifications List" as a root cause of actual violations, and the SDN List has *no predetermined update timetable*. A hard-coded list with no `as_of` mis-screens silently for months and reports itself clean throughout.
- **Comparing raw name strings and calling it fuzzy matching.** Punctuation alone sinks the canonical legal-form variant below threshold. The fix is normalisation, not a lower threshold — lowering the threshold to catch `P.J.S.C.` would flood the queue with genuinely unrelated names while still missing accent and word-order variants.
- **Treating an unresolved country as "not on any list".** This is the fail-open that never alerts. `""`, `"  KP  "`, `"IRAN"` and `"XX"` must not clear — a leading space on a North Korea code is otherwise enough to get through.
- **Letting NaN through an ownership gate.** Every comparison against NaN is `False`, so `nan >= 50.0` is `False` and a NaN ownership percentage passes the 50 Percent Rule check and reports `CLEARED`. Validate for finiteness, not just range.
- **Hard-coding an embargo list and never revisiting it.** Syria is the live example: Executive Order 14312 of 30 June 2025 revoked the Syria sanctions programme and OFAC removed the Syrian Sanctions Regulations (31 CFR part 542) from the CFR on 26 August 2025. Blocking every Syrian counterparty on the authority of a revoked programme is over-blocking; targeted Syria-related designations remain and are caught by *list* screening.
- **Encoding a territory as if it were a country.** A `"RU_CRIMEA"` pseudo-code is emitted by no upstream system, so a Crimea rule keyed on it can never fire — and Crimea is not in Russia's ISO namespace in any case. Territorial embargoes need ISO 3166-2.
- **Checking each blocked owner against 50% separately.** The whole point of OFAC's 2014 revision is aggregation. Per-owner comparison silently never triggers.
- **Declining a 50%-owned entity quietly.** That is a different legal event from blocking property and reporting it to OFAC. Getting the consequence wrong is its own violation.
- **Using `check()`.** It is a deprecated no-op shim that reads `data["valid"]` and echoes it back. It performs **no screening**. It now emits a `DeprecationWarning`; wiring it into a gate produces a confident "compliant" for a subject that was never screened against anything.

## Verification

- Screen `"VTB Bank P.J.S.C."` against a list holding `"VTB BANK PJSC"` and confirm `BLOCKED_SANCTIONS_HIT` at 100.0 via `EXACT_NAME` — and confirm the raw strings still score below 85%, so the fix is normalisation rather than a loosened threshold.
- Screen `"Société Générale S.A."` against `"SOCIETE GENERALE SA"` and confirm the accent variant blocks; screen `"PJSC Sberbank of Russia"` against `"SBERBANK OF RUSSIA PJSC"` and confirm `FUZZY_TOKEN_ORDER`.
- Screen `"Vneshtorgbank"` and confirm it blocks via the alias while reporting the primary name `"VTB BANK PJSC"`.
- Screen `"APPLE INC"` and confirm `CLEARED` with zero hits — normalisation must not have produced an everything-blocker.
- Screen country `"  KP  "`, `"Iran"`, `""`, `"XX"` and `"ATLANTIS"`: the first two block, the last three raise `SanctionsScreeningError` rather than clearing.
- Submit `float("nan")` and `float("inf")` ownership and confirm each raises rather than passing the 50% gate.
- Construct the engine with no snapshot, with `entries=()`, and with a string `as_of`; confirm all three raise.
- Screen two blocked owners at 25% each and confirm `BLOCKED_OFAC_50_PERCENT_RULE` with `aggregate_sanctioned_ownership_pct == 50.0` and `requires_ofac_blocking_report`; drop one to 24% and confirm the rule no longer fires.
- Confirm `"SY"` is absent from `DEFAULT_EMBARGOED_COUNTRIES` and a Syrian counterparty clears — then confirm a *designated* Syrian party still blocks on the list.
- Screen country `"UA"` with `region_code="UA-40"` and confirm `BLOCKED_EMBARGO` via `TERRITORY_EMBARGO`; screen `"UA"` with no region and confirm `REVIEW_REQUIRED` with a `NO_REGION_SUPPLIED` advisory; screen `"UA-46"` and confirm `CLEARED`.
- Screen a sectoral entry and confirm `RESTRICTED_SECTORAL` with `has_sanctions_hit` **False**.
- Screen against a snapshot 8 days old with `max_list_age_days=7` and confirm `REVIEW_REQUIRED`; at exactly 7 days confirm `CLEARED`; confirm staleness never softens a real block.
- Confirm the same entity designated on two lists under one identifier reports **two** hits, not one.
- Run `python -m unittest discover -s skills/sanctions-screening-for-counterparties-and-instruments/scripts` and confirm a 100% pass rate.

## Migration from 1.0.0

Version 2.0.0 changes the constructor and several names, because each old form had a fail-open path that could not be fixed compatibly:

| 1.0.0 | 2.0.0 | Why |
|---|---|---|
| `Engine(sanctions_database=[...])` | `Engine(SanctionsListSnapshot(entries, as_of))` | the list must carry a date; `None`/`[]` silently fell back to demo rows |
| `DEFAULT_SANCTIONED_DATABASE` | `DEMO_SANCTIONED_DATABASE` / `demo_snapshot()` | it was never a sanctions list, and the old name invited use as one |
| `EMBARGOED_COUNTRIES` | `DEFAULT_EMBARGOED_COUNTRIES` (+ `DEFAULT_EMBARGOED_TERRITORIES`) | now injectable, and territories are a separate ISO 3166-2 namespace |
| `report.hits: List` | `report.hits: Tuple` | the report is frozen so an audit record cannot be edited after the fact |
| `engine.check(...)` | `engine.screen_subject(...)` | `check()` still works but performs no screening and now warns |

`ComplianceResult`, `ScreeningSubject`, `SanctionsListType` and `ScreeningEntityKind` keep their names and import paths.

## Related Skills

- `kyc-aml-considerations-for-algo-trading-entities`
- `moscow-exchange-moex-api-integration`
- `on-chain-transaction-monitoring-for-anomalies`
- `record-retention-periods-by-jurisdiction`
- `regulatory-change-monitoring-service-integration`
- `cross-jurisdiction-regulatory-conflict-resolution`
