---
name: record-retention-periods-by-jurisdiction
description: >-
  Use when a storage lifecycle policy or archive migration is about to decide whether a
  trading record can be deleted, resolving the binding minimum retention across every
  regulator that governs it and the earliest date no rule still compels keeping it.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: regulatory-compliance-global
  tags: record-retention, compliance, sec-rule-17a-4, finra-4511, fca-sysc-9, mifid-ii, mas, asic, sebi, jurisdiction
  brokers_frameworks: "SEC Rule 17a-4 (US); FINRA Rule 4511 (US); CFTC Reg 1.31 (US); FCA SYSC 9 (UK); MiFID II Art. 16(6) (EU); MAS SFA (Singapore); Corporations Act 2001 (Australia); SEBI Stock Brokers Regulations (India); Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when a trading system holds records that more than one regulator can demand,
and something — a storage lifecycle policy, an archive migration, a cost-driven purge — is
about to decide whether a record can go.

`RecordRetentionPeriodsByJurisdictionEngine` in
`scripts/record_retention_periods_by_jurisdiction.py` takes a record's class, its creation
date, and **every** jurisdiction that binds it, resolves the applicable rule in each, and
returns the *latest* resulting `earliest_permissible_purge_date` together with the citation
that set it. It also reports the readily-accessible sub-period where the regime has one,
and flags where the firm's own configured retention duration falls short of the floor.

## When NOT to Use

- **As approval to delete anything.** `ELIGIBLE_FOR_REVIEW` means "no rule in the table
  still compels retention" — nothing more. Litigation holds, regulatory investigations,
  tax law, AML/CFT law, contractual commitments, and internal policy all extend retention
  independently of the securities rules modelled here.
- **As a legal determination.** The built-in table encodes generally applicable floors for
  the firm statuses named in each citation (SEC-registered broker-dealer, FINRA member,
  FCA common platform firm, MAS CMS licence holder, Australian company, SEBI-registered
  stock broker). Which one binds *your* entity is a question for counsel.
- **For record classes the table does not model.** Only the five `RecordClass` values are
  covered, at the granularity each citation states. A regime that separates sub-classes
  more finely needs your own rules passed to the `rules` constructor argument.
- **As a CFTC, tax, AML, or GDPR retention tool.** CFTC Regulation 1.31 is a different
  five-year regime with a one-year carve-out for oral pre-trade communications; tax and
  AML periods run separately; GDPR storage limitation pushes the other way. None are
  modelled.
- **As the storage tier or immutability decision.** That is
  `data-retention-policy-and-storage-tiering`; this skill supplies the retention floor it
  consumes.

## Prerequisites

- Per record: a stable `record_id`, a `RecordClass`, an ISO-8601 `creation_date`
  (`YYYY-MM-DD`, or a datetime with an explicit UTC offset), and **the full list** of
  jurisdictions that bind it.
- For any record whose applicable rule measures from an event other than creation — SEC
  Rule 17a-4(e)(5) runs from account closure — the date of that event as
  `clock_start_date`.
- A decision, recorded, on whether your competent authority has requested the extended
  period (five years to seven under SYSC 9.1.2R / MiFID II Art. 16(6)), passed as
  `extension_requested`.
- Confirmation from counsel that each built-in row in `references/standards.md` matches
  your entity's licence, membership, and activity — with overrides supplied where it does
  not.

## Workflow

1. **Classify the record before looking up any period.** A single per-country number is
   wrong for at least one class in that country: SEC Rule 17a-4 keeps blotters and ledgers
   six years but business communications three. Pick the `RecordClass` first; the
   jurisdiction lookup is second.

2. **List every binding jurisdiction, not the one that generated the record.** A trade
   booked in London for a US-registered entity is bound by both. `jurisdictions` takes a
   sequence, and the *latest* purge date across them governs — the engine will not silently
   pick the first match.

3. **Supply `clock_start_date` whenever the rule measures from an event.** A US client
   account record created in 2015 for an account closed in 2025 is retained until 2031, not
   2021. Without the closure date the engine returns `INDETERMINATE` rather than measuring
   from creation, because a wrong clock start is a silent multi-year error.

4. **Call `assess_all(records, as_of=date.today())`** and read the status per record:
   - `RETAIN` — a rule still compels retention; `days_until_eligible` says how long.
   - `ELIGIBLE_FOR_REVIEW` — route to a human disposition review. Never to a purge job.
   - `LEGAL_HOLD` — retain regardless; the computed floor is still reported so the hold's
     effect is visible in the audit trail.
   - `INDETERMINATE` — an unknown jurisdiction or a missing clock start. Retain, and fix
     the input or add a rule.

5. **Treat any `INDETERMINATE` as a blocker for the whole record.** If a record names two
   jurisdictions and one is unmodelled, the engine reports no purge date at all rather than
   the known jurisdiction's date. Do not work around this by dropping the unknown
   jurisdiction from the list.

6. **Honour `readily_accessible_until` separately from the purge date.** SEC Rule 17a-4(a)
   and (b) require the first two years in an easily accessible place. A record moved to
   deep archive at eighteen months is non-compliant even though it is years away from
   eligibility — hand this field to
   `data-retention-policy-and-storage-tiering`, which consumes exactly this input.

7. **Act on `policy_shortfall_years` before it becomes a deletion.** A configured retention
   of five years against a six-year floor does not fail today; it fails silently in year
   five when the lifecycle rule fires.

> Full procedure: see `references/workflows.md`.
> Per-regime periods, clock starts, and sources: see `references/standards.md`.
> Printable sign-off checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Believing there is one retention period per country.** The single most damaging
  assumption in this domain, and the defect this skill's version 1.0.0 shipped: it asserted
  a flat `US=7, UK=5, SG=5, AU=7, IN=8, EU=5`. Two of those figures had no basis in the
  rule cited — SEC Rule 17a-4 sets six years for ledgers and three for communications, not
  seven for everything, and India's eight years is a Companies Act 2013 s.128(5)
  obligation on books of account, not a SEBI period (SEBI (Stock Brokers) Regulations 1992
  reg. 18 is five years).
- **Measuring an account-linked record from its creation date.** Rule 17a-4(e)(5) runs six
  years from account closure. A 2015 record on an account closed in 2025 is a decade off
  if measured from creation, and the error is invisible in any report that shows only years.
- **Purging at five years under MiFID II or SYSC 9 when the authority asked for seven.**
  Art. 16(6) and SYSC 9.1.2R both allow the competent authority to require up to seven
  years. The engine defaults to five; pass `extension_requested` when yours has asked.
- **Approximating a year as 365 days.** Over a six-year window the drift is one to two
  days — enough to fire a purge job before the period has elapsed. The engine adds calendar
  years and maps 29 February onto 28 February.
- **Reading `ELIGIBLE_FOR_REVIEW` as a delete instruction.** It is the absence of a
  modelled obligation, not the presence of permission. Nothing in this engine sees your
  litigation holds unless you set `legal_hold`.
- **Dropping the jurisdiction the engine could not resolve.** Removing `"ZZ"` from the list
  turns an honest `INDETERMINATE` into a confident purge date for an obligation nobody
  checked.
- **Passing a bare string as `jurisdictions`.** `"US"` is a sequence of two characters;
  iterated, it becomes `("U", "S")` and matches no rule. The engine rejects it explicitly
  rather than reporting two unknown jurisdictions.
- **Feeding a naive timestamp.** `2019-03-15T23:30:00` is on either side of a year boundary
  depending on the zone. The engine rejects naive datetimes and normalises offset-aware
  ones to UTC before taking the date.
- **Assuming WORM storage is mandatory in the US.** SEC Rule 17a-4(f) has permitted an
  audit-trail alternative since the October 2022 amendments took effect on 3 January 2023.
- **Applying the SEC rows to CFTC-regulated activity.** Regulation 1.31 is five years, with
  oral pre-trade communications at one year and swap records running for the life of the
  swap plus five. Add CFTC rules rather than reusing the SEC ones.

## Verification

- Confirm the US ledger floor resolves to **6 years** with a 2-year readily-accessible
  sub-period citing 17a-4(a), and US communications to **3 years** citing 17a-4(b)(4) —
  the regression against version 1.0.0's flat 7-year figure
  (`test_us_ledger_floor_is_six_years_not_seven`).
- Confirm India resolves to **8 years** for `TRADE_AND_LEDGER` citing Companies Act
  s.128(5) and **5 years** for other classes citing SEBI reg. 18.
- Confirm a record bound by both `UK` (5) and `AU` (7) takes the **Australian** date, and
  that reversing the order of the list changes nothing.
- Confirm a record naming one unknown jurisdiction returns `INDETERMINATE` with
  `earliest_permissible_purge_date is None` — not the known jurisdiction's date — and that
  a policy shortfall is *not* reported against that partially resolved floor.
- Confirm a `CLIENT_ACCOUNT` record with no `clock_start_date` is `INDETERMINATE`, and that
  supplying a 2025 closure date for a 2015 record yields 2031.
- Confirm the boundary: on `creation + N years` exactly the record is
  `ELIGIBLE_FOR_REVIEW`, and one day earlier it is `RETAIN` with
  `days_until_eligible == 1`.
- Confirm 2020-02-29 + 6 years yields 2026-02-28, and that 2016-01-01 + 6 years yields
  2022-01-01 rather than the 2021-12-30 a 365-day approximation would give.
- Confirm `legal_hold` overrides an elapsed period, and that a bare-string `jurisdictions`,
  a naive datetime, a NaN policy figure, and a duplicated `record_id` are each rejected.
- Run `python -m unittest discover -s skills/record-retention-periods-by-jurisdiction/scripts`
  and confirm a 100% pass rate.

## Related Skills

- `record-keeping-requirements-for-tax-audit-defense`
- `data-retention-policy-and-storage-tiering`
- `best-execution-record-keeping-global`
- `data-localization-requirements-for-trade-records`
- `cross-jurisdiction-regulatory-conflict-resolution`
- `backtest-audit-trail-for-regulatory-review`
