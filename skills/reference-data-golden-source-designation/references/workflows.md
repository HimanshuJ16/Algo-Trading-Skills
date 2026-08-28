# Workflows for Reference Data Golden Source Designation

## 0. Designate before you ingest

A golden source designation is a governance decision recorded as configuration, not a
runtime inference. Before any code runs, for each field in the instrument master:

1. **Define the field.** BCBS 239 para 37 calls a data dictionary a *precondition*, and
   it is a practical one: a priority rule keyed on `tick_size` is undefined until the
   firm agrees whether that means the venue's minimum price increment, the vendor's
   display increment, or the increment applicable to this instrument's current tick
   band.
2. **Check whether the field has a registration authority.** ISIN (ANNA/NNAs), MIC
   (SWIFT), LEI (GLEIF) and CFI (ANNA) do. For those, the authority is the designation,
   and vendors rank below it as coverage or latency fallbacks — not against each other.
   See `references/standards.md`.
3. **Rank the remaining vendors, with a reason per rank.** "Exchange first for
   `tick_size` because the venue sets it" is a designation. "Bloomberg first because we
   have always used Bloomberg" is an incumbency, and it will not survive the first
   incident review.
4. **Record who owns the rule and when it is next reviewed.** RTS 23 Art. 5 has
   competent authorities re-assessing reference data content and accuracy at least
   quarterly for entities in its scope; an unreviewed rule set is a control that decays
   silently.

Configuration that omits a field is a decision too — the engine treats an unruled field
as ungoverned and refuses to fill it.

## 1. Multi-vendor ingestion

Collect one `VendorFieldData` per vendor per instrument:

- `vendor_name` must be unique within the call. Two snapshots from one vendor are
  **rejected**, not merged: last-wins would silently discard one, and the engine has no
  basis to decide which is authoritative.
- `fields` values must be `str` or `None`. Normalise upstream. A float `0.01` alongside
  a string `"0.01"` reads as a vendor disagreement, and in v1.0.0 the float was written
  into a record typed as `Dict[str, Optional[str]]`.
- `as_of` must be timezone-aware, and is required once `max_staleness` is configured. A
  naive timestamp from a vendor in another timezone misstates the record's age by the
  offset between them.

An empty `vendor_data` list raises, and so does a set of vendors that collectively supplied
no fields — the same failure wearing a different shape. Both are ingestion failures, not
reconciled records, and a `RESOLVED` status over zero fields reads downstream as "this
instrument reconciled cleanly".

## 2. Age gating, before ranking

If `max_staleness` is set, pass `evaluation_time` explicitly — the engine reads no clock,
so a report can be replayed exactly. Whole snapshots are then excluded when:

- the record is older than `max_staleness` (`VENDOR_RECORD_STALE`);
- `as_of` is absent (`VENDOR_AS_OF_MISSING`) — an undateable record cannot be shown to
  be current, and ranking it ahead of one that can be defeats the point of the gate;
- `as_of` is **after** `evaluation_time` (`VENDOR_AS_OF_IN_FUTURE`) — the vendor's clock
  or its timestamp field is wrong, so the record's age is unknown in an unknown
  direction.

The boundary is inclusive: a record aged exactly `max_staleness` is usable.

This gate is about **age**, not about **effective date**. A freshly published MIC record
may describe a change that takes effect on the fourth Monday of the month. Age gating
will pass it. See `references/standards.md`.

## 3. Eligibility, per field

A vendor's value for a field is eligible unless:

- it is `None` (`NULL`);
- it is empty or whitespace-only and `treat_blank_as_missing` is on, which is the default
  (`BLANK`);
- it matches a declared `missing_sentinels` entry after stripping and casefolding
  (`SENTINEL`);
- its whole snapshot was age-gated at step 2.

Declare sentinels per feed. `"N/A"` means absence in an ISIN column and could be a real
value in a free-text one, which is why the default sentinel set is empty.

## 4. Conflict detection

A field has a conflict when two or more **eligible** values differ under
`conflict_comparison`:

- `EXACT` (default) — byte equality. Reports `"USD"` vs `"usd"` as a disagreement.
- `CASEFOLD_STRIP` — compares stripped and casefolded. Suppresses casing and padding
  noise.

Neither mode reconciles `"0.01"` against `"0.0100"`; that needs a typed comparison this
string-oriented engine deliberately does not attempt. Normalisation changes only what
counts as a disagreement — the value written to the golden record is always the selected
vendor's string exactly as supplied.

Blanks are excluded before comparison, so a vendor sending `""` alongside another
sending `"USD"` is a coverage gap, not a conflict.

## 5. Priority resolution

For each field, walk `priority_rules[field]` in order and take the first vendor with an
eligible value. Falling *through* a ranked vendor's NULL to the next ranked vendor is
still governance — `resolution_rule` stays `PRIORITY_RULE` and `is_governed` stays
`True`.

If no ranked vendor supplied an eligible value:

| Situation | Finding | Default outcome |
|---|---|---|
| The field has no rule at all | `NO_PRIORITY_RULE` | No value written |
| The field has a rule, but only undesignated vendors supplied data | `NO_RULED_VENDOR_SUPPLIED_VALUE` | No value written |
| Every vendor the rule names is absent from this instrument's data | `UNKNOWN_VENDOR_IN_RULE` | No value written |
| No vendor supplied anything eligible | `FIELD_HAS_NO_USABLE_VALUE` | No value written |

`allow_undesignated_fallback=True` changes the outcome, not the finding: the engine picks
the lowest-sorting vendor with an eligible value, marks `resolution_rule =
UNGOVERNED_FALLBACK`, sets `is_governed=False`, and raises `UNGOVERNED_FALLBACK`. The
pick is deterministic so reports are reproducible — but determinism is not correctness,
and nothing here says that vendor is right.

**Why the default is to refuse.** v1.0.0 filled these cases from "the first non-null
value from any vendor", iterating a dict built from the caller's list. Over the same two
vendors supplied in opposite order it produced two different golden records, and stamped
both `RESOLVED` with a `golden_vendor` attached.

## 6. Reading the report

Branch on **`is_fully_governed`**, not on `status`. It is `True` only when every field in
the record was filled by a designated golden source. `status` is a one-line summary with
this precedence:

`UNGOVERNED_FIELDS` > `MISSING_DATA` > `CONFLICTS_FOUND` > `RESOLVED`

`CONFLICTS_FOUND` ranks last deliberately: a disagreement that a designated source
resolved is the engine working as intended, and such a record is still fully governed.
An ungoverned value ranks first because it is the failure that looks like a success.

`findings` is the actionable output. Per field, `FieldResolution` carries
`all_vendor_values` (raw, exactly as supplied — the audit record), `skipped_vendors`
(vendor → why it was ineligible) and `overridden_vendors` (eligible values that lost).

## 7. Persist the resolution, not just the record

Store the `FieldResolution` list alongside the golden record. For trading venues and
systematic internalisers, RTS 23 Art. 6(2) requires arrangements that identify
previously submitted reference data that was incomplete or inaccurate and correct it
without undue delay — which is impossible if all you retained was the winning value. For
everyone else the same record is what makes a bad-tick-size incident a ten-minute
investigation instead of a week of vendor email.

## 8. Review the designation on a cadence

Re-check each rule when a vendor's coverage or quality changes, when a vendor is renamed
or replaced (watch for `UNKNOWN_VENDOR_IN_RULE` — it usually means the rules were not
updated after a rename), and on a fixed calendar. A designation is a control, and an
unreviewed control is an assumption.
