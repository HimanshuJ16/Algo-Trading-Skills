# Workflows — sanctions-screening-for-counterparties-and-instruments

The deep procedure behind `SKILL.md`. Each step names the failure it exists to
prevent, because every one of them was a live defect in version 1.0.0 of this
skill.

## 0. Decide what you are screening and against what

Two inputs, and neither has a safe default:

- **The subject.** A counterparty (`COUNTERPARTY`) or an instrument issuer
  (`INSTRUMENT_ISSUER`). For an instrument, screen the **issuer**, not the
  ticker: the ISIN identifies the paper, the designation attaches to the entity.
- **The list.** A `SanctionsListSnapshot` you construct from your own feed, with
  an `as_of` date.

The engine has **no default list**. `DEMO_SANCTIONED_DATABASE` is five fixtures
for the test suite; `demo_snapshot()` wraps them and says so in its `source`
field. Version 1.0.0 wrote `self.database = sanctions_database or DEFAULT_...`,
which meant a caller whose feed load returned `[]` — the shape a failed HTTP
fetch usually takes — screened live counterparties against five hand-written rows
and received `CLEARED`. `SanctionsListSnapshot` now rejects an empty entry list,
and the constructor rejects `None`.

## 1. Ingest and normalise the subject

```python
from datetime import date
from sanctions_screening_for_counterparties_and_instruments import (
    ScreeningSubject, ScreeningEntityKind, SanctionedOwner, build_engine,
)

engine = build_engine(entries=my_ofac_entries, as_of=date(2026, 8, 28),
                      source="OFAC SDN + SSI, pulled 2026-08-28")

subject = ScreeningSubject(
    subject_id="549300XXXXXXXXXXXX01",       # LEI, ISIN, or internal id
    name="VTB Bank P.J.S.C.",
    country_iso="RU",
    entity_kind=ScreeningEntityKind.COUNTERPARTY,
    region_code=None,                         # ISO 3166-2 where known
    sanctioned_owners=(),                     # itemised, not pre-aggregated
)
report = engine.screen_subject(subject, screened_on=date(2026, 8, 28))
```

**Fail-closed validation runs first.** A blank `subject_id`, a name with no
alphanumeric content, a non-finite or out-of-range ownership percentage, a
duplicate `owner_id`, a malformed `region_code`, or a subdivision that does not
belong to the declared country each raise `SanctionsScreeningError`. None of them
return a report. A screen that could not evaluate its input must not emit an
artefact indistinguishable from a clean screen.

### Country normalisation

`normalize_country` resolves alpha-2, common alpha-3, and common English names to
ISO 3166-1 alpha-2, and raises otherwise — including on `""`, whitespace-only
values, and `XX`/`ZZ`/`N/A`-style "unknown jurisdiction" placeholders, each of
which is a syntactically plausible token that would otherwise screen clean
against every list.

Concretely, under version 1.0.0 all of these returned `CLEARED`:

| Input | Old result | Why |
|---|---|---|
| `"  KP  "` | `CLEARED` | `.upper()` without `.strip()`; a space cleared North Korea |
| `"IRAN"` | `CLEARED` | namespace mismatch against a set holding `"IR"` |
| `""` | `CLEARED` | missing data treated as absence of risk |
| `"XX"` | `CLEARED` | placeholder accepted as a real country |

## 2. Resolve the subdivision before trusting a Ukraine clear

The Crimea and DNR/LNR embargoes are territorial. Everything they cover reports
country `UA`. A country-code screen therefore *structurally cannot* see them, and
no threshold or list refresh fixes that.

- Supply `region_code` as ISO 3166-2 — `UA-43` (Crimea), `UA-40` (Sevastopol),
  `UA-14` (Donetsk), `UA-09` (Luhansk).
- If you cannot resolve it, the engine returns `REVIEW_REQUIRED` with a
  `NO_REGION_SUPPLIED` advisory. That is a deliberate refusal to issue a clear it
  cannot justify — do not treat it as a pass.
- `UA-14` and `UA-09` are **over-approximations**: E.O. 14065 designates the
  "Covered Regions", not the whole oblasts. Expect false positives there and
  triage them; the alternative is a false negative.

## 3. Screen the name — normalise, then measure

Order matters. Normalisation happens **before** the distance metric, on both the
subject and every list name (list names are normalised once at construction, not
once per screen).

Pipeline: Unicode NFKD accent fold → uppercase → delete punctuation → collapse
whitespace. Alphanumerics of **every** script survive; an ASCII-only filter would
normalise a Cyrillic name to the empty string and turn a designated entity into a
blank that matches nothing.

Then two scores, and the higher wins:

| Method | Catches |
|---|---|
| `FUZZY_EDIT_DISTANCE` | normalised Levenshtein — typos, minor spelling variants |
| `FUZZY_TOKEN_ORDER` | edit distance after alphabetically sorting tokens — legal form moved from prefix to suffix |

Plus `EXACT_NAME` (identical after normalisation) and `EXACT_IDENTIFIER` (LEI /
ISIN / tax ID), which always score 100.0.

### Why normalisation and not a lower threshold

`SKILL.md` has always given `"VTB Bank PJSC"` vs `"VTB Bank P.J.S.C."` as the
canonical pitfall. On raw strings that pair scores **76.47%** — four inserted
dots over a 17-character maximum. Under the documented 85% threshold version
1.0.0 returned `CLEARED` for a designated Russian bank: the skill's own headline
example was a false negative.

Lowering the threshold to 76% would be the wrong fix twice over. It would admit a
large volume of genuinely unrelated names — the false-positive flood the skill
also warns about — while still missing accent variants and word-order variants,
which are not close in edit distance either. After normalisation the pair is
*identical*, and the same change fixes `Société Générale` and `PJSC Sberbank of
Russia` for free.

### Aliases

`SanctionedEntry.aliases` is screened alongside `name`. Designated entities are
published under multiple a.k.a. names and the name a counterparty gives you is
often the a.k.a. rather than the primary. A hit found via an alias reports the
**primary** name in `matched_sanctioned_name`, with the alias in `reason`, so the
audit trail names the designation rather than the spelling.

### Performance

Screening one subject against a realistic list means tens of thousands of
comparisons — the SDN List alone runs to that scale once aliases are counted. A
length-ratio bound (`min/max`, an upper bound on achievable edit similarity)
short-circuits candidates that could not clear the threshold at any spelling,
before the O(n·m) inner loop. Measured: ~56 ms per subject against a 34,000-name
index. The bound can only skip candidates the full computation would have scored
below it, and the test suite asserts that against an unfiltered reference.

## 4. Apply the OFAC 50 Percent Rule with aggregation

```python
subject = ScreeningSubject(
    ..., sanctioned_owners=(
        SanctionedOwner("OWNER_X", "BLOCKED PERSON X", 25.0),
        SanctionedOwner("OWNER_Y", "BLOCKED PERSON Y", 25.0),
    ),
)
# -> BLOCKED_OFAC_50_PERCENT_RULE, aggregate 50.0%
```

Prefer the itemised form. OFAC aggregates across blocked persons, so comparing
each holder to 50% individually never fires — which is exactly the shape of the
scalar `ownership_pct_by_sanctioned` field version 1.0.0 offered as its only
input.

The scalar is retained for callers who have already aggregated. Supplying both
takes the **maximum**, not the sum: a caller who provides both is almost
certainly expressing one stake two ways, and summing would double-count it into a
spurious block.

**Then act on the right consequence.** At or above 50% the entity *is* blocked
property whether or not it is listed — `report.requires_ofac_blocking_report` is
`True`, meaning block and report to OFAC. Below 50%, a blocked minority holder is
a reason to decline. Getting that distinction wrong is its own violation, which
is why the two live in different statuses.

## 5. Classify the designation before deciding what to do

`SanctionsProgram.BLOCKING` and `SanctionsProgram.SECTORAL` produce different
statuses and are never merged. A sectoral (SSI) match returns
`RESTRICTED_SECTORAL` and leaves `has_sanctions_hit` **False** — the entity is
not blocked, defined transaction types are. Route it to the desk rule that
governs those transactions rather than to the blocking workflow.

## 6. Check the list's age, and read the advisories

`STALE_SANCTIONS_LIST` fires when `screened_on - list_as_of > max_list_age_days`,
downgrading a clear to `REVIEW_REQUIRED`. It never downgrades a block: stale data
cannot make a real hit go away.

A future-dated snapshot raises rather than screening — it means the clock or the
feed is wrong, and neither is a condition to issue clears under.

## 7. Consume the report

| Field | Use |
|---|---|
| `status` | Highest-precedence finding; drives the gate |
| `hits` | **Every** finding, sorted by descending score — a block never hides a second problem |
| `advisories` | Reasons a negative result may not be reliable |
| `requires_ofac_blocking_report` | Block-and-report obligation, distinct from declining |
| `aggregate_sanctioned_ownership_pct` | The aggregation actually applied |
| `screened_on` / `list_as_of` | Reproducibility; both belong in the audit record |

Precedence: `BLOCKED_OFAC_50_PERCENT_RULE` → `BLOCKED_SANCTIONS_HIT` →
`BLOCKED_EMBARGO` → `RESTRICTED_SECTORAL` → `REVIEW_REQUIRED` → `CLEARED`.

Gate on `status`, but persist `hits` and `advisories`. A gate that stores only
the status discards the evidence that the screen was ever capable of producing a
different answer.

## 8. Do not call `check()`

`check()` reads `data["valid"]` and echoes it back. It performs **no screening**.
It survives only so version 1.0.0 imports keep working, it now emits a
`DeprecationWarning`, and its docstring says all of this. An agent wiring up
something called "compliance check" on a sanctions engine and getting
`ComplianceResult(True, "Valid")` has built a gate that passes everything.

## 9. Operational integration

- **Screen at onboarding and re-screen on list change**, not only at onboarding.
  A counterparty clean on Monday is designated on Tuesday, and nothing about the
  counterparty record changes to tell you.
- **Re-screen the whole book after each list refresh**, not just new subjects.
- **Persist the report**, including `list_as_of` and the `source` string. "Which
  list did this clear come from" is the first question asked after an incident.
- **Alert on the advisory channel separately.** `REVIEW_REQUIRED` volume rising
  is how you find out a feed stopped updating.
- **Keep the threshold calibration under version control** with its testing
  evidence — Wolfsberg asks for documented rationale and independent testing, and
  a number in a config file with no record of how it was chosen does not satisfy
  that.
