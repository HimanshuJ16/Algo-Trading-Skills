# Pre-Flight Checklist

## Designation
- [ ] Does **every** field in the instrument master have an explicit priority rule, with a recorded reason per rank?
- [ ] For fields that have a **registration authority** — ISIN (ANNA/NNAs), MIC (SWIFT), LEI (GLEIF), CFI (ANNA) — is the authority the designated source, rather than two vendors being ranked against each other?
- [ ] Is each field **defined** (BCBS 239 para 37's data dictionary) before a source was designated for it?
- [ ] Is there a named owner and a review date for the rule set?
- [ ] Has the rule set been checked for vendors that no longer exist under that name? (`UNKNOWN_VENDOR_IN_RULE`)

## Ingestion integrity
- [ ] Is `vendor_data` non-empty, **and** does at least one vendor actually carry fields? Both empty shapes are ingestion failures, not resolved records.
- [ ] Is each vendor present **at most once**, with deduplication decided upstream?
- [ ] Are all field values `str` or `None`, so that `0.01` and `"0.01"` cannot read as a vendor disagreement?
- [ ] Are `as_of` and `evaluation_time` timezone-aware?

## Absence handling
- [ ] Is `treat_blank_as_missing` left **on**, so a top-ranked vendor's empty string cannot beat a lower-ranked vendor's real value?
- [ ] Are `missing_sentinels` declared **per feed** from that feed's documented conventions, rather than guessed globally?
- [ ] Has it been confirmed that no declared sentinel is a legitimate value in any field it will be applied to?

## Staleness
- [ ] Is `max_staleness` set, or has it been consciously accepted that the report says nothing about whether the winning record was current?
- [ ] Is `evaluation_time` passed explicitly, so the report can be replayed?
- [ ] Were `VENDOR_AS_OF_MISSING` findings investigated rather than tolerated? A feed that never sets `as_of` is excluded entirely once the gate is on.
- [ ] Is it understood that age gating is **not** effective-date handling — a fresh MIC record can describe a change that takes effect on the fourth Monday of the month?

## Conflict semantics
- [ ] Is `conflict_comparison` set deliberately? `EXACT` will report `"USD"` vs `"usd"`; `CASEFOLD_STRIP` will not.
- [ ] Is it understood that **neither** mode reconciles `"0.01"` against `"0.0100"`?
- [ ] Has it been confirmed that normalisation affects only conflict *detection*, never the value stored?

## Governance of the output
- [ ] Does the consuming code branch on **`is_fully_governed`**, not on `status`?
- [ ] Is `allow_undesignated_fallback` left **off**, or, if on, is `is_governed=False` actually honoured downstream rather than only `golden_record` being read?
- [ ] Is it understood that the fallback's determinism is reproducibility, **not** correctness?
- [ ] Are `MISSING_DATA` fields treated as holes to fill, rather than as fields that happen to be empty?

## Auditability
- [ ] Is the full `resolutions` list persisted alongside the golden record, not just the winning values?
- [ ] Can you answer, for any field in production, *which vendor supplied it, under which rule, and what was rejected*?
- [ ] For a trading venue or systematic internaliser: do these arrangements let you **identify** previously submitted reference data that was incomplete or inaccurate, per RTS 23 Art. 6(2)?

## Scope
- [ ] Is it clear this engine reconciles **static reference data fields**, not prices? (Use `multi-source-price-reconciliation-tie-breaking` for quotes and marks.)
- [ ] Is it clear it does not map identifiers between vendor symbologies? (Use `reference-data-symbol-mapping-across-vendors`.)
- [ ] Is it clear it does not detect changes over time? (Use `reference-data-change-notification-pipeline`.)
- [ ] Is it clear the engine performs no I/O, holds no state, and reads no clock — every guarantee is about the inputs handed to it?

## Migration from v1.0.0
- [ ] Have fields that previously resolved via the silent arbitrary fallback been identified? They will now surface as `MISSING_DATA` with `NO_PRIORITY_RULE` or `NO_RULED_VENDOR_SUPPLIED_VALUE`, and the fix is to write the rule, not to enable the fallback.
- [ ] Have callers of the removed `Engine` / `Config` classes been updated? `Engine.process()` was an identity function.
- [ ] Do callers handle `GoldenSourceInputError` on empty vendor data, duplicate vendors, and non-string values, all of which v1.0.0 accepted silently?
