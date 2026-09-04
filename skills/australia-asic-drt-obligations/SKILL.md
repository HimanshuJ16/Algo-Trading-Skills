---
name: australia-asic-drt-obligations
description: >-
  Use when a Reporting Entity under the ASIC Derivative Transaction Rules 2024 must
  validate an OTC derivative report before submission: ISO 17442 legal entity
  identifiers with check digits, ISO 23897 transaction identifiers and ISO 4914 product
  identifiers.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: regulatory-compliance-global
  tags: compliance, asic, australia, otc-derivatives, trade-reporting, lei, uti, upi
  brokers_frameworks: generic
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when your desk is a **Reporting Entity** under the **ASIC Derivative Transaction Rules (Reporting) 2024** (F2022L01706, commenced 21 October 2024) and executes OTC derivatives — interest rate, FX, credit, equity or non-electricity commodity derivatives — that must be reported to a Licensed or Prescribed Derivative Trade Repository.

`AsicDrtReportingEngine` is the gate that runs after enrichment and before ISO 20022 serialisation. Every check traces to the rule that requires it:

- **Rule 2.2.3(1)** — report "by no later than the end of the second Business Day after the day on which the Reportable Transaction or change occurs" (T+2).
- **Rule 2.2.3(3)** — T+4 instead, where a value for **Item 92 of Table S1.1(1)** (Package identifier) is required — **except** for "a foreign exchange contract that is part of a foreign exchange swap derivative transaction", which stays at T+2.
- **Rule 2.2.3(2)** — where the repository was unavailable by the deadline, the obligation becomes "as soon as practicable after" it becomes available. No substitute deadline is specified, so the engine flags this for human assessment rather than asserting a breach.
- **Table S1.1(1) Item 1** — the UTI, "As specified in ISO 23897".
- **Table S1.1(1) Item 2** — the UPI, "As specified in ISO 4914"; "not required in a report about the termination of an OTC Derivative".
- **Table S1.1(1) Items 7 and 8** — the counterparty identifier: an ISO 17442 LEI, or, where the identifier type indicator is False, "an alphanumeric code of not more than 72 characters".
- **Table S1.1(1) Item 92** — the package identifier, "an alphanumeric code of not more than 100 characters".

## When NOT to Use

- **Outside the Australian regime.** These rules bind Reporting Entities under Part 7.5A of the Corporations Act. For the EU use `mifid-ii-algo-trading-compliance-eu`; a transaction reportable in several jurisdictions is not discharged by this gate — see `cross-jurisdiction-regulatory-conflict-resolution`.
- **As a completeness check for the report.** Table S1.1(1) carries around 100 data elements plus valuation (S1.1(2)) and collateral (S1.1(3)) tables. This engine checks four identifiers and the deadline. Passing it does not mean the report is complete or accurate.
- **As proof that an identifier is real.** The checks are structural. A structurally valid LEI may be lapsed rather than "current" as Items 5, 6 and 23 require; a structurally valid UPI may not exist in the DSB UPI reference data library. Confirm against GLEIF and the DSB, not against this module.
- **As a UPI check-character validator.** ISO 4914 clause 4 requires the twelfth character to be a check character computed per Annex C (a MOD 31,30 scheme). Annex C is normative but not publicly available, so it is deliberately **not** implemented here rather than guessed.
- **For exchange-traded derivatives.** Rule 1.2.4 excludes Derivatives able to be traded on a Part 7.2A Market or a Regulated Foreign Market from the definition of OTC Derivative.

## Prerequisites

- Python 3.10+ (standard library only).
- The middle office must supply, per trade: the counterparty identifier and its **Item 8 type indicator** (`counterparty_identifier_is_lei`), the UTI, the UPI (from the Derivatives Service Bureau), and — where the transaction is one leg of a separately reported economic arrangement — the Item 92 package identifier.
- **The business-day calendar of the Relevant Jurisdiction.** Rule 1.2.3 defines a Business Day as "a day that is not a Saturday, a Sunday, or a public holiday or bank holiday in the **Relevant Jurisdiction**", and Relevant Jurisdiction is Australia only where the transaction was booked to the P&L of an Australian branch or entered into in Australia; otherwise it is the jurisdiction where it was booked or entered into. Pass that jurisdiction's holidays as `holidays`. Omitting them skips weekends only, which overstates the deadline and suppresses genuine late flags.
- **Date contract:** `trade_date`, `reporting_date` and every element of `holidays` must be plain `datetime.date` objects in the Relevant Jurisdiction's local calendar. `datetime` instances are rejected with `TypeError` — Item 103 (Reporting timestamp) is reported in UTC, so a UTC timestamp near midnight resolves to a different local day, and a `datetime` sitting in a holiday set never compares equal to the `date` being tested, silently overstating the deadline.
- **Identifier contract:** identifier fields are `str` or `None`. A non-string — a numeric Client Code, a `Decimal` — raises `TypeError` rather than being coerced, because `str()`-ing it would validate a value different from the one the serialiser will emit. Convert at the system boundary. Surrounding whitespace is stripped.
- Identifier formats the enrichment layer must satisfy:
  - **LEI (ISO 17442)** — 20 characters: 18 uppercase alphanumeric plus **2 numeric check digits** satisfying ISO/IEC 7064 MOD 97-10 (`value % 97 == 1`).
  - **UTI (ISO 23897)** — 20–52 uppercase alphanumeric characters, no separators (`18!c2!n32c`: the generating entity's LEI followed by up to 32 transaction-specific characters).
  - **UPI (ISO 4914)** — 12 characters: the fixed `QZ` prefix, nine characters and one check character, each from A–Z and 0–9 excluding the vowels A, E, I, O, U and the character Y.

## Workflow

1. **Trade capture.** The OTC derivative is executed and booked in the OMS with its trade date and Relevant Jurisdiction.
2. **Enrichment.** Middle office attaches the counterparty identifier, determines the UTI generating entity under Rule 2.2.9 and obtains the UTI, and fetches the UPI from the DSB.
   - **Decision point — is the counterparty identifier actually an LEI?** Set `counterparty_identifier_is_lei=False` for a natural person not eligible for an LEI per the ROC Statement (report the Client Code), or for the anonymity identifier `ANON` on a CCP-cleared transaction where the counterparties were not disclosed to each other. Forcing an LEI check on those blocks a legitimate report.
   - **Decision point — is this a termination report?** Set `is_termination_report=True`. Item 2 does not require a UPI for a termination; demanding one manufactures a false exception. A UPI that *is* supplied is still validated.
3. **Classify the deadline before validating.** Set `requires_package_identifier=True` where the transaction is one of two or more reported separately as one economic arrangement, and set `is_fx_swap_leg=True` where it is an FX contract forming part of an FX swap.
   - **Decision point — the FX-swap leg is the trap.** Item 92(c) requires a package identifier for exactly the case Rule 2.2.3(3) excludes from T+4. An FX swap reported as two contracts with different expiration dates needs the package identifier **and** keeps the T+2 deadline. Treating it as T+4 silently under-reports a breach.
4. **Validate.** Call `AsicDrtReportingEngine.validate_report(trade, reporting_date, holidays, repository_unavailable_at_deadline)`, or `batch_validate` for the evening sweep.
5. **Route exceptions.** `is_ready_for_reporting=False` sends the trade to the middle-office exception queue with `missing_fields` naming the failing Table S1.1(1) item. Do not serialise a failing trade.
6. **Assess lateness.** `is_late_submission=True` escalates for late-reporting remediation — **unless** `repository_outage_relief_may_apply=True`.
   - **Decision point — do not self-report a Rule 2.2.3(2) outage as a breach.** Where the repository was unavailable at the deadline, the obligation is to report "as soon as practicable" once it is available. That is a judgement about facts the engine does not hold; it flags the case and leaves the determination to compliance.
7. **Serialise and submit.** Passing trades go to the ISO 20022 XML pipeline (Rule 2.2.4(2)) and on to the trade repository.

> Full step-by-step procedure: see `references/workflows.md`.
> Rule-by-rule regulatory map with source links: see `references/standards.md`.
> Printable sign-off checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Granting T+4 to an FX-swap leg.** Rule 2.2.3(3) extends the deadline for transactions requiring an Item 92 value "other than a foreign exchange contract that is part of a foreign exchange swap derivative transaction". Because Item 92(c) names that exact case, the naive reading — "package identifier required, therefore T+4" — is wrong for the most common package there is, and it hides a real T+2 breach.
- **Calendar days instead of business days.** `timedelta(days=2)` mis-flags Friday trades and ignores holiday closures. T+2/T+4 are counted in Business Days.
- **Assuming the calendar is always Sydney.** Rule 1.2.1 fixes references to *time* to AEST/AEDT in Sydney, and Rule 2.2.9(4) says "business day in Sydney" for UTI generating-entity tie-breaks — but the Rule 2.2.3 deadline runs on Business Days in the **Relevant Jurisdiction**, which for a transaction booked to an offshore branch is not Australia.
- **Requiring an LEI for every counterparty.** Item 6 (Counterparty 1) must be a current LEI, but Item 7 (Counterparty 2) and Item 10 (Beneficiary 1) expressly allow a Client Code, a Designated Business Identifier or `ANON`. A gate that rejects those blocks compliant reports.
- **Requiring a UPI on a termination report.** Item 2 excludes it.
- **Treating structural validity as sufficiency.** An LEI that is 20 uppercase alphanumeric characters is not necessarily valid — ISO 17442 requires 2 **numeric** check digits satisfying MOD 97-10. And a UPI passing the prefix/alphabet test can still fail the ISO 4914 Annex C check character, which this engine does not compute. Both must be confirmed against GLEIF and the DSB.
- **Short UTIs.** ISO 23897 UTIs are 20–52 characters. Accepting a shorter value lets a structurally invalid identifier through to the repository.
- **Reporting against a UTC timestamp.** Item 103 is UTC; the deadline is a local-calendar day count. A submission at 23:40 local on the deadline is on time even though its UTC timestamp falls on the following day — and the reverse also happens.
- **Treating a repository outage as a breach, or as an indefinite excuse.** Rule 2.2.3(2) neither preserves the original deadline nor sets a new one; "as soon as practicable" has to be evidenced.
- **Missing the Item 92 value itself.** Knowing a package identifier is required does not supply one. A required-but-absent Item 92 value is a rejected report, not a late one.

## Verification

- Fully populated trade ⟹ `is_ready_for_reporting=True`, `missing_fields == []`, `repository_outage_relief_may_apply=False`.
- **LEI:** 20 uppercase alphanumeric characters failing MOD 97-10 ⟹ rejected; a value that passes MOD 97-10 but ends in letters instead of 2 numeric check digits ⟹ rejected; a lowercase LEI ⟹ rejected.
- **Non-LEI counterparty identifier** (`counterparty_identifier_is_lei=False`): `CLIENT0001` and `ANON` ⟹ accepted; 72 characters ⟹ accepted, 73 ⟹ rejected; empty ⟹ rejected; a non-ASCII digit such as `١` ⟹ rejected even though `str.isalnum()` accepts it.
- **UTI:** 20 and 52 characters ⟹ accepted; 19 and 53 ⟹ rejected; lowercase ⟹ rejected.
- **UPI:** missing `QZ` prefix, a vowel, the character `Y`, or a length other than 12 ⟹ rejected.
- **Termination report:** absent UPI ⟹ accepted; a malformed UPI supplied anyway ⟹ still rejected.
- **Package identifier:** required and absent ⟹ rejected naming Item 92; 100 characters ⟹ accepted, 101 ⟹ rejected; a malformed value is rejected even when not required.
- **Deadline (business days):** Monday trade ⟹ T+2 Wednesday; Friday trade ⟹ T+2 the following Tuesday; a Tuesday holiday pushes a Monday trade's deadline to Thursday; a Saturday trade starts its count on the next business day.
- **Boundary:** `reporting_date == deadline` ⟹ not late; one day later ⟹ late.
- **T+4 vs the carve-out:** Friday trade requiring an Item 92 value ⟹ deadline the following Thursday; the same trade with `is_fx_swap_leg=True` ⟹ deadline the following Tuesday and late when reported on the Wednesday.
- **Rule 2.2.3(2):** late report with `repository_unavailable_at_deadline=True` ⟹ `is_late_submission=True` **and** `repository_outage_relief_may_apply=True`; an on-time report with the same flag ⟹ both False.
- **Input contract:** `reporting_date` before `trade_date` ⟹ `ValueError`; a `datetime` or a string for either date ⟹ `TypeError`; a `datetime` or a string inside `holidays` ⟹ `TypeError`; a non-string identifier (e.g. `1234567890`) in any of the four identifier fields ⟹ `TypeError`; a negative business-day offset ⟹ `ValueError`.
- **Whitespace:** identifiers surrounded by spaces are stripped and accepted.
- Run `python -m unittest discover -s skills/australia-asic-drt-obligations/scripts` and confirm all tests pass.

## Related Skills

- `mifid-ii-algo-trading-compliance-eu`
- `asic-market-integrity-rules-automated-trading`
- `cross-jurisdiction-regulatory-conflict-resolution`
- `record-retention-periods-by-jurisdiction`
- `isin-cusip-sedol-cross-reference-service`
