# Standards — sanctions-screening-for-counterparties-and-instruments

## Jurisdiction and scope

The binding rules below are **US (OFAC)**, with the **EU**, **UN** and **UK**
regimes supported as list *inputs* rather than modelled in their own right. A US
person's obligations are not a European or British firm's obligations, and this
engine does not resolve which regime binds you. Nothing here is legal advice, and
no threshold in the engine substitutes for a compliance officer's determination.

> **Corrections to version 1.0.0 of this skill.** The previous standards table
> made three claims that were wrong, and all three are corrected below and in the
> engine:
>
> 1. It stated that Levenshtein similarity "**MUST** be calibrated to ≥ 85.0%".
>    No regulator or standards body prescribes any numeric match threshold. 85.0
>    is an engineering default; see §3.
> 2. It listed **SY** among jurisdictions that "MUST be hard-blocked". The US
>    comprehensive Syria programme was revoked in 2025; see §4.
> 3. It listed **`RU_CRIMEA`** as a country code. No such code exists in any ISO
>    namespace and no upstream system emits it, so the rule could never fire. The
>    Crimea/DNR/LNR embargoes are territorial and need ISO 3166-2; see §5.

## 1. The OFAC 50 Percent Rule — aggregation, and a distinct consequence

Under OFAC's **Revised Guidance of 13 August 2014**, an entity owned "in the
aggregate, directly or indirectly, **50 percent or more** by one or more blocked
persons" is itself considered a blocked person, **whether or not it appears on
the SDN List**.

The 2014 revision *is* the aggregation. Under OFAC's earlier position the entity
had to be owned by a single blocked person for the rule to bite; since 2014,
Blocked Person X at 25% plus Blocked Person Y at 25% blocks the entity. An
implementation that compares each blocked owner to 50% individually therefore
never triggers the rule at all.

Two consequences the engine deliberately keeps apart:

| Situation | Consequence |
|---|---|
| A blocked person holds a **minority** interest | A reason to decline the relationship |
| Blocked persons hold **≥ 50% in the aggregate** | The entity **is** blocked property — block it and report to OFAC; quietly declining is not sufficient |

This is why `BLOCKED_OFAC_50_PERCENT_RULE` outranks a list hit in the status
precedence and why the report exposes `requires_ofac_blocking_report`.

The rule is about **ownership**. OFAC separately cautions about entities that
blocked persons may *control* without majority ownership; the engine does not
model control, and a control relationship is a matter for your compliance
officer, not for this threshold.

Sources: [OFAC — Entities Owned by Blocked Persons (50 Percent Rule) FAQs](https://ofac.treasury.gov/faqs/topic/1521),
[OFAC Revised Guidance, 13 August 2014](https://ofac.treasury.gov/media/6186/download?inline=),
[Crowell & Moring — OFAC Changes Its Mind on Aggregate SDN Ownership](https://www.crowell.com/en/insights/client-alerts/ofac-changes-its-mind-on-aggregate-sdn-ownership).

## 2. Blocking vs sectoral designations — not interchangeable

| Designation | Effect | Engine status |
|---|---|---|
| **Blocking** (OFAC SDN and equivalents) | Property and interests in property are blocked; dealings prohibited | `BLOCKED_SANCTIONS_HIT` |
| **Sectoral** (OFAC **SSI** List, introduced with the July 2014 Ukraine/Russia programmes) | Only **defined transaction types** are prohibited — typically new debt of specified tenors and new equity — with an entity that otherwise remains tradable | `RESTRICTED_SECTORAL` |

Wolfsberg describes the sectoral programmes as "prohibiting certain types of
transactions with targeted entities in the finance, energy and defence sectors,
as well as entities owned by 50% or more by the targets", and notes that for
sectoral programmes "only a defined subset" of activity is caught — in contrast
to list-based blocking programmes where the red flag is simply the presence of
the name.

Collapsing the two in either direction is an error with real cost: treating SSI
as blocking over-blocks lawful business, and treating a blocking designation as
sectoral is a violation. Note that the 50 Percent Rule applies to sectoral
targets too.

Sources: [Wolfsberg Guidance on Sanctions Screening (2019), glossary and §2.2](https://db.wolfsberg-group.org/assets/4b6c2db6-696d-492e-bdd5-c51552708597/Wolfsberg%20Guidance%20on%20Sanctions%20Screening.pdf),
[OFAC — Ukraine-/Russia-related Sanctions](https://ofac.treasury.gov/sanctions-programs-and-country-information/ukraine-russia-related-sanctions).

## 3. Match thresholds are a risk decision, not a rule

**No regulator or standards body prescribes a fuzzy match threshold.** The
previous version of this skill asserted a mandatory ≥ 85.0%; that was invented.

Wolfsberg's 2019 sanctions screening guidance places calibration squarely in the
risk-assessment pillar — "applying risk based decisions to resolve specific
questions of what data attributes to screen, when to screen, what lists to use
and **how exact or 'fuzzy' to set the screening filter**", with the requirement
that "the decision making and governance structure needs to be clearly
articulated, documented and supported by analysis and testing". It further asks
for "a governance framework [containing] the documented rationale for risk based
decisions, such as those made in support of the creation of screening rules and
threshold settings", and for "independent risk based testing" that the filter
"generates expected alerts ... in accordance with the FI's risk appetite".

OFAC's 2019 Framework says the same thing from the other side: "To the extent
technology solutions are part of an organization's internal controls, solutions
should be **calibrated to the organization's risk profile**."

Neither document contains a number. The engine's `85.0` is a starting point to be
calibrated against your own list and book and recorded — not a compliance claim.

What *is* evidence-backed is that raw-string comparison is inadequate regardless
of threshold. OFAC's Framework lists among the root causes of actual violations
screening software that fails "to update ... to incorporate updates to the SDN
List or the Sectoral Sanctions Identifications List", fails to include "pertinent
identifiers", or "did not account for **alternative spellings** of prohibited
parties or countries (i.e., Habana instead of Havana)". Wolfsberg frames the same
space as "alphabets, languages, cultures, spelling, abbreviations, acronyms and
aliases". That is why this engine normalises before measuring distance and
screens published aliases — those changes have an evidentiary basis; the number
does not.

Sources: [Wolfsberg Guidance on Sanctions Screening (2019)](https://db.wolfsberg-group.org/assets/4b6c2db6-696d-492e-bdd5-c51552708597/Wolfsberg%20Guidance%20on%20Sanctions%20Screening.pdf),
[OFAC — A Framework for OFAC Compliance Commitments (May 2019)](https://ofac.treasury.gov/media/16331/download?inline=),
[Davis Polk — OFAC Publishes Guidance on Sanctions Compliance Programs](https://www.davispolk.com/insights/client-update/ofac-publishes-guidance-sanctions-compliance-programs).

## 4. Comprehensive country embargoes — and the Syria correction

US programmes that embargo an entire jurisdiction rather than designating named
persons, as verified on **2026-08-28**:

| Country | ISO | Status |
|---|---|---|
| Cuba | CU | Comprehensive |
| Iran | IR | Comprehensive |
| North Korea (DPRK) | KP | Comprehensive |
| ~~Syria~~ | ~~SY~~ | **No longer comprehensive — removed 2025** |

**Executive Order 14312 of 30 June 2025**, "Providing for the Revocation of Syria
Sanctions", revoked the six executive orders underlying the Syrian Sanctions
Program (E.O. 13338, 13399, 13460, 13572, 13573 and 13582) and terminated the
underlying national emergency, effective **1 July 2025**. OFAC then published a
final rule removing the **Syrian Sanctions Regulations, 31 CFR part 542**, from
the Code of Federal Regulations on **26 August 2025**.

Syria-related sanctions did not disappear — they became *targeted*. Designations
under authorities such as E.O. 13894 remain in force against Assad, captagon
networks, human-rights abusers and chemical-weapons and proliferation actors, and
restrictions on Syrian military, police and intelligence services persist. Those
are caught by **list** screening, which is where they belong. A hard-coded `"SY"`
country embargo blocks every Syrian counterparty on the authority of a programme
that no longer exists.

This table is a default with a verification date, not a feed. Programmes change;
re-verify it rather than trusting its age.

Sources: [E.O. 14312 — Providing for the Revocation of Syria Sanctions (30 June 2025)](https://www.govinfo.gov/app/details/DCPD-202500725),
[OFAC — Publication of Final Rule to Remove the Syria Sanctions Regulations (25 August 2025)](https://ofac.treasury.gov/recent-actions/20250825),
[Baker McKenzie — OFAC and BIS Issue Final Rules Removing Syria Sanctions Regulations](https://sanctionsnews.bakermckenzie.com/ofac-and-bis-issue-final-rules-removing-syria-sanctions-regulations-and-relaxing-export-controls-for-syria/),
[OFAC — Syria Sanctions FAQs](https://ofac.treasury.gov/faqs/topic/1571).

## 5. Territorial embargoes need ISO 3166-2, not a country code

The Crimea and DNR/LNR embargoes are **territorial**, and this is a structural
problem for any screen that compares country codes only:

| Authority | Territory | ISO 3166-2 |
|---|---|---|
| E.O. 13685 | Autonomous Republic of Crimea | `UA-43` |
| E.O. 13685 | Sevastopol | `UA-40` |
| E.O. 14065 | Donetsk oblast — **Covered Regions only** | `UA-14` |
| E.O. 14065 | Luhansk oblast — **Covered Regions only** | `UA-09` |

E.O. 13685 prohibits most new investment, trade and services in the Crimea
region. E.O. 14065 prohibits new investment in, and the import from and export
to, the "Covered Regions" — the so-called DNR and LNR and such other regions as
the Secretary of the Treasury may determine — amounting in effect to a complete
trade embargo on those regions. OFAC has been explicit that E.O. 14065 targets
the **Covered Regions**, not the entire Donetsk and Luhansk oblasts, so an
oblast-level code is an over-approximation and the engine documents it as one.

Every one of these territories is internationally recognised as part of Ukraine
and carries the ISO 3166-1 country code **`UA`**; ISO 3166-2:RU contains no codes
for them. So a counterparty in Sevastopol reports `UA` and a country-code screen
clears it every time. The previous version's `"RU_CRIMEA"` was not a code in any
namespace, and nothing upstream emits it — the rule could never fire.

The engine therefore takes an optional ISO 3166-2 `region_code`, and returns
`REVIEW_REQUIRED` rather than `CLEARED` when the country contains embargoed
territories and no subdivision was supplied.

Sources: [OFAC — Ukraine-/Russia-related Sanctions](https://ofac.treasury.gov/sanctions-programs-and-country-information/ukraine-russia-related-sanctions),
[OFAC FAQ 1006 — Covered Regions under E.O. 14065](https://ofac.treasury.gov/faqs/1006),
[ISO 3166-2:UA](https://en.wikipedia.org/wiki/ISO_3166-2:UA).

## 6. List currency — an undated list is an untestable control

The OFAC SDN List has **no predetermined update timetable**; names are added and
removed as necessary, and changes publish through the Recent Actions page as they
occur. There is consequently no "correct" snapshot age that can be derived from
the rules — only a risk decision that must be made and recorded.

What is not a matter of judgement is that failing to refresh is a documented
cause of violations: OFAC's Framework names, among the root causes of screening
failures, organisations that "fail to update their screening software to
incorporate updates to the SDN List or the Sectoral Sanctions Identifications
List".

So `SanctionsListSnapshot.as_of` is mandatory, and the engine raises a
`STALE_SANCTIONS_LIST` advisory — downgrading a clear to `REVIEW_REQUIRED` —
once the snapshot exceeds `max_list_age_days`. It never downgrades a *block*.

Sources: [OFAC FAQ 20 — How often is the SDN List updated?](https://ofac.treasury.gov/faqs/20),
[OFAC — Recent Actions](https://ofac.treasury.gov/recent-actions),
[OFAC — A Framework for OFAC Compliance Commitments (May 2019)](https://ofac.treasury.gov/media/16331/download?inline=).

## 7. Source lists and their current publishers

| Engine `SanctionsListType` | Publisher | Note |
|---|---|---|
| `OFAC_SDN` | US Treasury OFAC | Blocking designations |
| `OFAC_SSI` | US Treasury OFAC | Sectoral Sanctions Identifications — restricted dealings, not blocking |
| `EU_CONSOLIDATED` | European Commission | "Consolidated list of persons, groups and entities subject to EU financial sanctions", distributed via the Financial Sanctions Files (FSF) service |
| `UN_SANCTIONS` | UN Security Council | UN Security Council Consolidated List |
| `UK_HMT` | FCDO | **Name is historical.** The OFSI Consolidated List of Asset Freeze Targets **closed on 28 January 2026** and is no longer updated; the **UK Sanctions List**, maintained by the FCDO, is now the single source for UK designations. The enum member is retained for API stability; its meaning is "UK". |

Sources: [GOV.UK — The UK Sanctions List](https://www.gov.uk/government/publications/the-uk-sanctions-list),
[Akin — Consolidating the Consolidated List: the UK Moves to a Single Sanctions List](https://www.akingump.com/en/insights/alerts/consolidating-the-consolidated-list-the-uk-moves-to-a-single-sanctions-list),
[European Commission — EU Sanctions Map / FSF](https://www.sanctionsmap.eu/),
[UN Security Council Consolidated List](https://www.un.org/securitycouncil/content/un-sc-consolidated-list).

## 8. Engineering defaults (no regulatory basis)

No regulator prescribes any of these. They are policy inputs — calibrate them,
record the calibration, and re-test after any change.

| Parameter | Default | Meaning |
|---|---|---|
| `fuzzy_match_threshold_pct` | 85.0 | Name match score at or above which a hit is raised. Validated to `(0, 100]`: at or below 0 every entry matches every subject, above 100 nothing ever matches — both silently disable the control |
| `max_list_age_days` | 7 | Snapshot age beyond which a clear is downgraded to `REVIEW_REQUIRED` |
| `DEFAULT_EMBARGOED_COUNTRIES` | `{CU, IR, KP}` | Verified 2026-08-28; see §4 |
| `DEFAULT_EMBARGOED_TERRITORIES` | 4 ISO 3166-2 codes | See §5; `UA-14`/`UA-09` over-approximate the Covered Regions |
| `OFAC_AGGREGATE_BLOCKING_THRESHOLD_PCT` | 50.0 | **Fixed by OFAC guidance, not a policy dial** |

## 9. Decision precedence

`status` reports the highest-precedence finding; `report.hits` always carries
every finding, so a block never conceals a second problem:

`BLOCKED_OFAC_50_PERCENT_RULE` → `BLOCKED_SANCTIONS_HIT` → `BLOCKED_EMBARGO` →
`RESTRICTED_SECTORAL` → `REVIEW_REQUIRED` → `CLEARED`.

`REVIEW_REQUIRED` is not a hit. It means the screen ran but its *negative* result
cannot be relied on — a stale list, or Ukraine exposure with no subdivision
resolved. Treating it as a clear reintroduces exactly the silent failure this
skill exists to prevent.

## 10. Known limitations

State these where the screening methodology is documented; they are the honest
boundary of what this engine detects.

- **No phonetic matching** (Soundex, Metaphone, NYSIIS).
- **No cross-script transliteration.** A Cyrillic, Arabic or Han name will not
  match a Latin list entry. Names are preserved rather than mangled, but they are
  not romanised.
- **No control-without-ownership test** for the 50 Percent Rule.
- **No date-of-birth, address, nationality or identifier-document matching** to
  discriminate individuals sharing a name — the engine screens names and
  identifiers only, so individual screening will over-alert.
- **Oblast-level over-approximation** for `UA-14` / `UA-09` versus the Covered
  Regions actually designated.
- **No licence or general-authorisation logic.** A hit says a designation
  matched, not that a dealing is prohibited.
