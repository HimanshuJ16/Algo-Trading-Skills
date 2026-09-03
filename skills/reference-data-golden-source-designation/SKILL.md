---
name: reference-data-golden-source-designation
description: >-
  Use when several vendors describe the same instrument and one record must go into the
  master, resolving each field by priority-ranked source with a recorded basis, and
  refusing to guess when no rule applies.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: data-management-global
  tags: golden-source, reference-data, data-governance, conflict-resolution, multi-vendor, authoritative-source, instrument-master, bcbs-239, mifir-rts-23
  brokers_frameworks: "BCBS 239 (Principle 3, para 36(d)); MiFIR RTS 23 (Commission Delegated Regulation (EU) 2017/585); ISO 6166 ISIN (ANNA); ISO 10383 MIC (SWIFT); ISO 17442 LEI (GLEIF); Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when several vendors describe the same instrument — Bloomberg against LSEG/Refinitiv against an exchange direct feed — and one record has to go into the instrument master. It answers two questions that are routinely collapsed into one:

> What value goes in this field, and what is the basis for it being that one?

A golden source designation is the recorded answer to the second question: for each field, which source is authoritative, in what order, and why. `GoldenSourceDesignationEngine` applies those designations, reports every disagreement it resolved, and — the part that matters — reports every field where **no designation applied**, rather than filling it from whichever vendor happened to be first in the list.

That distinction is the reason for this module's existence. Reference data errors do not announce themselves. A wrong `tick_size` produces rejected orders at a venue you rarely trade. A wrong `lot_size` produces a position a fraction of the intended size. A stale `isin` produces settlement breaks weeks after the trade. In every case the pipeline reported success, because a value was present.

Two fields in this space have **regulator-designated** sources rather than merely preferred vendors: MiFIR RTS 23 Art. 3 requires trading venues and systematic internalisers to obtain the ISO 6166 ISIN before trading commences, and requires LEIs to be ISO 17442 codes listed in the GLEIF database. Where a registration authority exists — ISIN, MIC, LEI, CFI — ranking two vendor redistributors against each other is solving the wrong problem. See `references/standards.md`.

## When NOT to Use

- **For prices, quotes or marks.** This engine compares strings for equality and has no concept of tolerance, tick size or outlier distance. Three vendors quoting 100.01 / 100.02 / 100.02 is not a string conflict problem. Use `multi-source-price-reconciliation-tie-breaking`, which does median-distance outlier attribution and separates "usable" from "corroborated".
- **To map identifiers between vendor symbologies.** Deciding that `AAPL`, `AAPL.O` and `US0378331005` are the same instrument happens *before* this engine runs. That is `reference-data-symbol-mapping-across-vendors`. This engine assumes you already know which vendor rows describe the same instrument.
- **To detect that reference data changed.** This resolves one point-in-time snapshot set. Comparing today's record against yesterday's — FB → META, a lot size revision, an exchange migration — is `reference-data-change-notification-pipeline`.
- **For fields with a scheduled effective date, on its own.** `max_staleness` gates records by **age**, not by when a change takes effect. The ISO 10383 registration authority publishes the MIC list on the second Monday of each month with changes effective on the fourth Monday, so a record can be fresh, authoritative, and still not yet applicable. Carry the effective date in your own schema.
- **With `allow_undesignated_fallback=True` and only `golden_record` read downstream.** The fallback marks its output `is_governed=False`, but nothing enforces that a consumer looks. If your downstream reads the dict and ignores the resolutions, the flag is decoration and you have re-created the v1.0.0 defect with extra steps.
- **As a durable system of record.** The engine holds nothing between calls and writes nothing. Retention, access control and operator identity — the parts of an audit trail a regulator asks about — are the caller's responsibility. Pair with `data-lineage-tracking-for-audit-and-debugging`.
- **As authority for a control in a firm BCBS 239 does not bind.** BCBS 239 applies to G-SIBs (and, at national discretion, D-SIBs), covers risk management data, and says a bank should "strive towards" a single authoritative source. It is worth following. It is not a rule you can cite at a non-systemic firm as a requirement.

## Prerequisites

- **A designation decision per field, made before any code runs**, expressed as `GoldenSourceConfig.priority_rules` (`field_name` → vendor names, highest priority first). A field absent from this mapping is ungoverned and will not be filled.
- Multi-vendor snapshots as `VendorFieldData(vendor_name, fields, as_of)`. Vendor names unique per call; field values `str` or `None`; `as_of` timezone-aware.
- Optional: `max_staleness` (a positive `timedelta`), which makes `evaluation_time` a required argument — the engine reads no clock.
- Optional: `missing_sentinels` declared **per feed** from that feed's documented conventions (`"N/A"`, `"NULL"`, `"-"`).
- Python 3.7+. No third-party dependencies.

## Workflow

1. **Designate before ingesting.** For each field: define what it means (BCBS 239 para 37 calls a data dictionary a precondition, and it is a practical one — `tick_size` is undefined until the firm agrees whether it means the venue's minimum increment or a vendor's display increment); check whether a registration authority exists for it; then rank vendors, with a reason per rank. "Exchange first for `tick_size` because the venue sets it" is a designation; "Bloomberg first because we always have" is an incumbency.
2. **Reject inputs that cannot support a record rather than repairing them.** `resolve_golden_record` raises `GoldenSourceInputError` on empty `vendor_data` **or vendors that collectively supplied no fields** (an absent feed is an ingestion failure, not a reconciled record — v1.0.0 returned `RESOLVED` with an empty record), a duplicated `vendor_name` (last-wins silently destroys one snapshot, and which is authoritative is your decision, not the engine's), a non-string value (a float `0.01` beside a string `"0.01"` reads as a vendor disagreement), and a naive `as_of` (a vendor in another timezone misstates its record's age by the offset).
3. **Gate snapshots by age, before ranking.** Priority ranks vendors, not snapshots: a rule putting Bloomberg first selects a three-month-old Bloomberg record over this morning's exchange record unless age is checked first. With `max_staleness` set, a snapshot is excluded when it is older than the window, when `as_of` is absent (undateable is not provably current), or when `as_of` is *after* `evaluation_time` (the vendor's clock is wrong, so the age is unknown in an unknown direction). The boundary is inclusive.
4. **Gate values by eligibility, before comparing them.** `None`, whitespace-only (default on), and declared sentinels are all "not supplied". Without this, a top-ranked vendor's `""` beats a lower-ranked vendor's real ISIN — the documented null-handling pitfall reintroduced through the back door.
5. **Detect conflicts among eligible values only.** `EXACT` (default) reports `"USD"` vs `"usd"`; `CASEFOLD_STRIP` suppresses casing and padding noise. Neither reconciles `"0.01"` against `"0.0100"` — that needs a typed comparison this string-oriented engine deliberately does not attempt. Normalisation changes what counts as a disagreement, never the value stored.
6. **Resolve by rank, and refuse when rank does not decide.** Falling through a ranked vendor's NULL to the next ranked vendor is still governance. But when no ranked vendor supplied an eligible value — or the field has no rule at all — the default is to write **nothing** and raise a finding (`NO_PRIORITY_RULE`, `NO_RULED_VENDOR_SUPPLIED_VALUE`, `UNKNOWN_VENDOR_IN_RULE`, `FIELD_HAS_NO_USABLE_VALUE`). `allow_undesignated_fallback=True` will fill it deterministically (lowest-sorting vendor) and label it `is_governed=False`, but that is a reproducible guess, not a designation.
7. **Branch on `is_fully_governed`, not on `status`.** It is `True` only when every field was filled by a designated source. `status` precedence is `UNGOVERNED_FIELDS` > `MISSING_DATA` > `CONFLICTS_FOUND` > `RESOLVED` — conflicts rank last on purpose, because a disagreement a designated source resolved is the engine working, and such a record is still fully governed.
8. **Persist the resolutions, not just the record.** For venues and SIs, RTS 23 Art. 6(2) requires arrangements that *identify* previously submitted reference data that was incomplete or inaccurate and correct it without undue delay — impossible if all you kept was the winning value. For everyone else it is the difference between a ten-minute investigation and a week of vendor email.

> Full procedure: see `references/workflows.md`.
> Regulatory scope, registration authorities, and what is *not* mandated: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Filling an ungoverned field from "the first non-null vendor".** This is the defect this version exists to remove. v1.0.0 iterated a dict built from the caller's `vendor_data` list, so the winner was decided by argument order: over the same two vendors supplied in opposite order it produced two different golden records, and stamped both `RESOLVED` with a `golden_vendor` attached. An arbitrary pick that carries a vendor attribution is indistinguishable downstream from a designated one.
- **Assuming an undesignated vendor is a reasonable fallback.** If the rule names Bloomberg and only Refinitiv supplied the field, v1.0.0 took Refinitiv's value and attributed it as golden. The rule said Bloomberg. The correct output is a hole and a finding.
- **Treating "not None" as "populated".** Vendors encode absence as `""`, whitespace, `"N/A"` and `"NULL"` at least as often as SQL NULL. A blank from a top-ranked vendor silently wins under an `is not None` test.
- **Declaring sentinels globally.** `"N/A"` is absence in an ISIN column and could be a legitimate value in a free-text one. Declare per feed, from that feed's documentation.
- **Ranking two redistributors for a field that has a registration authority.** Bloomberg and LSEG both redistribute ISINs, MICs and LEIs; neither issues them. The designation for those fields is ANNA/the NNA, SWIFT, and GLEIF respectively.
- **Letting priority stand in for recency.** A priority rule ranks vendors, not snapshots. Without age gating, a three-month-old top-ranked record beats this morning's second-ranked one, and the report looks identical either way.
- **Reading a passing staleness gate as "safe to apply now".** Age and effective date are different questions. A MIC record published on the second Monday describes a change effective on the fourth.
- **Merging two snapshots from the same vendor.** v1.0.0 silently kept the last one and the report showed only that one, so the discarded snapshot left no trace anywhere.
- **Treating `CONFLICTS_FOUND` as a failure.** Vendors disagreeing and a designated source settling it is the engine doing its job. The status worth alerting on is `UNGOVERNED_FIELDS`, and the field worth gating on is `is_fully_governed`.
- **Storing only `golden_record`.** The winning value alone cannot answer "which vendor supplied this, under which rule, and what did we reject" — which is both the RTS 23 Art. 6(2) obligation for entities in scope and the only useful artifact during an incident.
- **Citing BCBS 239 as a mandate.** Para 36(d) says a bank should "**strive towards**" a single authoritative source, for **risk data per type of risk**, and binds G-SIBs. Overstating it in a design document invites a reviewer to discover the overstatement and distrust the rest.

## Verification

- Priority beats list order: Bloomberg and Refinitiv both supply `isin` with `["Bloomberg", "Refinitiv"]` ranked $\implies$ Bloomberg's value wins, and reversing the argument list produces an identical `golden_record` (v1.0.0's unruled path did not).
- Governed NULL fall-through: Bloomberg supplies `isin=None`, Refinitiv supplies a value $\implies$ Refinitiv selected with `resolution_rule == PRIORITY_RULE` and `skipped_vendors == {"Bloomberg": "NULL"}` $\implies$ falling through a rank is governance, not fallback.
- Regression against the arbitrary fallback: a field with no rule $\implies$ `golden_record[field] is None`, `NO_PRIORITY_RULE` raised, `status == MISSING_DATA`, `is_fully_governed` `False`. v1.0.0 wrote the value and reported `RESOLVED`.
- Regression against order-dependence: vendors `Zeta` and `Alpha` supplying different values for an unruled field, with fallback enabled $\implies$ identical records in both argument orders (`Alpha` wins on sort). v1.0.0 returned `'1'` forward and `'100'` reversed.
- Regression against undesignated attribution: rule names Bloomberg, only Refinitiv supplies the field $\implies$ no value written and `NO_RULED_VENDOR_SUPPLIED_VALUE` raised. v1.0.0 returned Refinitiv's value with `status == RESOLVED`.
- Blank gating: Bloomberg (rank 1) supplies `"   "`, Refinitiv supplies a real ISIN $\implies$ Refinitiv's value selected, `skipped_vendors["Bloomberg"] == "BLANK"`. With `treat_blank_as_missing=False` $\implies$ `"   "` is selected, confirming the gate is what changes the outcome.
- Sentinel gating: `missing_sentinels={"N/A"}` and a vendor supplying `" n/a "` $\implies$ skipped as `SENTINEL` (stripped, casefolded); with no sentinels declared, `"N/A"` is a real value.
- Blanks are not conflicts: `"USD"` against `""` $\implies$ `has_conflict` `False`, `conflicts_detected == 0`.
- Comparison modes: `"USD"` against `" usd "` $\implies$ conflict under `EXACT`, no conflict under `CASEFOLD_STRIP`, and `golden_record` holds `"USD"` unchanged in both. `"0.01"` against `"0.0100"` $\implies$ conflict under `CASEFOLD_STRIP` too.
- Staleness: `max_staleness=1 day`, Bloomberg (rank 1) aged 30 days, Exchange aged 5 minutes $\implies$ Exchange selected, `VENDOR_RECORD_STALE` raised. Aged exactly 1 day $\implies$ usable; 1 day + 1 second $\implies$ excluded. Absent `as_of` $\implies$ `VENDOR_AS_OF_MISSING`; `as_of` an hour in the future $\implies$ `VENDOR_AS_OF_IN_FUTURE`. An `as_of` expressed in +05:30 $\implies$ compared on the absolute instant.
- Empty-snapshot rejection: vendors present but every `fields` dict empty $\implies$ `GoldenSourceInputError`, not a zero-field `RESOLVED` report.
- Finding scope: a rule set covering `isin`, `lot_size`, `cfi` and `mic` against an instrument reporting only a governed `isin` $\implies$ **no** findings at all, confirming `UNKNOWN_VENDOR_IN_RULE` does not fire for fields the instrument simply lacks.
- Input rejection: empty `vendor_data`, blank `instrument_id`, a duplicated vendor, a float field value, a blank vendor or field name, and a naive `as_of` or `evaluation_time` each raise `GoldenSourceInputError` (a `ValueError` subclass). `max_staleness` set without `evaluation_time` also raises.
- Config rejection: a vendor listed twice in one rule, a bare string where a vendor list belongs, a blank vendor name, an unknown `conflict_comparison`, a non-positive `max_staleness`, and a non-`GoldenSourceConfig` passed to the constructor each raise `GoldenSourceConfigError`.
- Report integrity: `all_vendor_values` preserves raw input including blanks; an absent vendor is distinguishable from one that sent `None`; fields are reported in sorted order; `conflicts_detected`, `fields_without_data` and `ungoverned_field_count` each equal the corresponding count over `resolutions`; two successive calls on different instruments do not contaminate each other.
- Status precedence: a record with one conflict *and* one ungoverned field $\implies$ `UNGOVERNED_FIELDS`.
- Run `python -m unittest discover -s skills/reference-data-golden-source-designation/scripts`.

## Related Skills

- `reference-data-symbol-mapping-across-vendors`
- `reference-data-change-notification-pipeline`
- `multi-source-price-reconciliation-tie-breaking`
- `data-vendor-cross-validation-for-backtests`
- `data-lineage-tracking-for-audit-and-debugging`
- `isin-cusip-sedol-cross-reference-service`
- `instrument-universe-change-detection-and-alerting`
- `vendor-outage-fallback-data-source-hierarchy`
- `data-quality-monitoring-dashboard`
