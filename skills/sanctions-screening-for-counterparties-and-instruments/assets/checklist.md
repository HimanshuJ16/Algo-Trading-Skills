# Pre-Flight / Sign-off Checklist — sanctions-screening-for-counterparties-and-instruments

Subject: ____________________  Screening date: __________  Reviewer: __________

Subject id (LEI/ISIN/internal): ____________________  Kind: COUNTERPARTY / INSTRUMENT_ISSUER

## List sourcing

- [ ] Snapshot obtained from the **actual publisher or vendor** — OFAC SDN, OFAC SSI, EU FSF, UN Consolidated, UK Sanctions List (FCDO).
- [ ] `DEMO_SANCTIONED_DATABASE` is **not** in the screening path. It is five test fixtures; screening against it yields a clean report and no coverage.
- [ ] Snapshot `as_of` date recorded on the report, and `source` names the feed and pull time.
- [ ] Snapshot age is within `max_list_age_days`, or the `STALE_SANCTIONS_LIST` advisory has been actioned rather than dismissed.
- [ ] Feed-load failure path verified to **fail the screen**, not to return an empty list. An empty list clears everything.
- [ ] UK designations sourced from the **UK Sanctions List** — the OFSI Consolidated List closed on 28 January 2026 and is no longer updated.

## Subject data quality

- [ ] `subject_id` present and non-blank.
- [ ] Name captured as the counterparty presents it, before any internal cleanup that might drop a legal form.
- [ ] Country resolved to **ISO 3166-1 alpha-2** before comparison; no free-text country reached the screen.
- [ ] No `XX` / `ZZ` / `N/A` placeholder passed as a country — each is a valid-looking token that screens clean against every list.
- [ ] For an instrument: the **issuer** was screened, not just the ticker or ISIN string.

## Territorial exposure

- [ ] `region_code` (ISO 3166-2) supplied wherever known.
- [ ] **Any Ukraine exposure**: subdivision resolved. `UA` alone cannot detect Crimea/DNR/LNR — every affected entity reports `UA`.
- [ ] Any `NO_REGION_SUPPLIED` advisory resolved before the subject was treated as cleared.
- [ ] `UA-14` / `UA-09` hits triaged against the actual **Covered Regions** — the oblast codes over-approximate what E.O. 14065 designates.

## Name matching

- [ ] Fuzzy threshold **calibrated for this list and book**, not accepted at the 85.0 default because it was there.
- [ ] Calibration rationale and testing evidence recorded and version-controlled (Wolfsberg asks for documented rationale and independent testing).
- [ ] Published **aliases / a.k.a. names** loaded into `SanctionedEntry.aliases`, not discarded at ingest.
- [ ] Punctuation and accent variants confirmed to match — screen a known dotted legal form (e.g. `P.J.S.C.`) as a live test.
- [ ] Known limitation accepted and compensated: **no phonetic matching, no cross-script transliteration.** Non-Latin-script names need a vendor or manual review.
- [ ] For individuals: date of birth / nationality / identifier checked **outside** this engine — it matches names and identifiers only and will over-alert on common names.

## Ownership — OFAC 50 Percent Rule

- [ ] Blocked owners itemised via `sanctioned_owners`, one record per blocked person.
- [ ] Holdings **aggregated across blocked persons** — 25% + 25% blocks the entity; per-owner comparison never triggers.
- [ ] Indirect holdings through intermediate vehicles traced and included.
- [ ] Ownership percentages verified finite and in range — a NaN passes a `>= 50` gate silently.
- [ ] **If aggregate ≥ 50%**: property **blocked and reported to OFAC**, not merely declined. `requires_ofac_blocking_report` actioned.
- [ ] If a blocked person holds a **minority** stake: declined on that basis, and the different consequence understood.

## Designation classification

- [ ] Each hit classified **BLOCKING** vs **SECTORAL** before deciding what to do.
- [ ] Sectoral (SSI) hits routed to the restricted-transaction rule, **not** the blocking workflow — the entity remains otherwise tradable.
- [ ] No blocking designation downgraded to sectoral.

## Decision and record

- [ ] `screened_on` passed explicitly; the run reproduces.
- [ ] `REVIEW_REQUIRED` treated as **not cleared**. It means the negative result is unreliable, not that the subject passed.
- [ ] **All** `hits` persisted, not just `status` — a block must not conceal a second finding.
- [ ] `advisories` persisted and monitored as their own alert channel; rising volume is how a stalled feed surfaces.
- [ ] `check()` confirmed absent from every gate — it performs no screening and returns a pass.
- [ ] Screening evidence archived with the onboarding or instrument-admission file.

## Ongoing

- [ ] Re-screening scheduled on **list change**, not only at onboarding — a clean counterparty is designated without its own record changing.
- [ ] Whole book re-screened after each list refresh, not only new subjects.
- [ ] Embargo defaults re-verified against primary sources since the recorded verification date; programmes are revoked as well as added (Syria, 2025).
- [ ] Retention period confirmed for the screening record in each applicable jurisdiction.

## Sign-off

- [ ] Reviewer: ____________________  Date: __________
- [ ] MLRO / Compliance officer: ____________________  Date: __________
- [ ] Counsel consulted where a licence, general authorisation, or the applicable regime is contested.
