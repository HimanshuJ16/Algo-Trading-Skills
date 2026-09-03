# Checklist for ASIC DRT Compliance

Rule references: ASIC Derivative Transaction Rules (Reporting) 2024 (F2022L01706, as amended).
See `references/standards.md` for the rule map, source links and the currency note.

## Identifiers — Table S1.1(1)

- [ ] **Item 7 (LEI path):** the counterparty LEI is 20 characters, uppercase alphanumeric,
      ends in **2 numeric check digits**, and passes the ISO/IEC 7064 MOD 97-10 check. A 20-char
      uppercase string is not enough — and neither is a MOD 97-10 pass with alphabetic check
      digits.
- [ ] **Item 8 set correctly:** `counterparty_identifier_is_lei=False` where the identifier is a
      Client Code (natural person not LEI-eligible per the ROC Statement), a Designated Business
      Identifier, or `ANON` for an anonymous CCP-cleared transaction — with the non-LEI value an
      alphanumeric code of at most 72 characters.
- [ ] **LEI currency confirmed against GLEIF.** Items 5, 6 and 23 require the *current* LEI; a
      lapsed LEI passes every structural check here.
- [ ] **Item 1:** the UTI is 20–52 uppercase alphanumeric characters with no separators
      (ISO 23897).
- [ ] **Item 2:** the UPI is 12 characters — `QZ` prefix plus 9 base characters and 1 check
      character from A–Z and 0–9 excluding A, E, I, O, U and Y (ISO 4914 clause 4).
- [ ] **UPI confirmed against the DSB reference data library.** The ISO 4914 Annex C check
      character is *not* verified by this engine.
- [ ] **Item 2 exemption applied:** no UPI is demanded on a report about the termination of an
      OTC Derivative (`is_termination_report=True`).
- [ ] **Item 92:** a package identifier (alphanumeric, ≤ 100 characters) is present wherever the
      transaction is one of two or more reported separately as a single economic arrangement.

## Deadline — Rule 2.2.3

- [ ] T+2/T+4 are counted in **Business Days**, not calendar days.
- [ ] The `holidays` set is the public and bank holidays of the **Relevant Jurisdiction**
      (Rule 1.2.3) — not unconditionally Sydney, and not empty. Rule 1.2.1's Sydney reference
      governs *times*, not the Business Day calendar.
- [ ] T+4 is applied only where an Item 92 value is required **and** the transaction is not a
      foreign exchange contract forming part of an FX swap (Rule 2.2.3(3)).
- [ ] **FX-swap legs requiring an Item 92 value are reported on T+2.** Confirm the pipeline sets
      `is_fx_swap_leg=True` for them; the naive "package identifier ⇒ T+4" reading hides a real
      breach.
- [ ] Trades reported exactly on the deadline are not flagged late
      (`reporting_date == reporting_deadline`).
- [ ] `reporting_date` is the Relevant-Jurisdiction local date, derived from the local calendar
      day rather than the UTC Item 103 timestamp.
- [ ] `trade_date`, `reporting_date` and every element of `holidays` are plain `datetime.date`
      objects — a `datetime` in the holiday set is silently ignored by set membership and would
      overstate the deadline, so the engine rejects it outright.
- [ ] Identifier fields are strings at the system boundary; a numeric or `Decimal` identifier is
      rejected rather than coerced, so the value validated is the value serialised.
- [ ] Where a trade repository outage caused a missed deadline,
      `repository_unavailable_at_deadline=True` was passed, the resulting
      `repository_outage_relief_may_apply` flag was reviewed by a person, and the outage window
      plus the actual submission time are recorded as evidence of "as soon as practicable"
      (Rule 2.2.3(2)).

## Process

- [ ] No trade with `is_ready_for_reporting=False` is serialised to the ISO 20022 pipeline.
- [ ] Exception-queue triage is keyed on the Table S1.1(1) item named in `missing_fields`.
- [ ] Lifecycle reports under Rule 2.2.2 (modification, termination, assignment, valuation,
      collateral, UTI change) are each validated against their own Rule 2.2.3 deadline, measured
      from the day the change occurs.
- [ ] Report completeness against the full Table S1.1(1), S1.1(2) and S1.1(3) is assured
      elsewhere — this gate covers four identifiers and the deadline only.
- [ ] Run test suite: `python -m unittest discover -s skills/australia-asic-drt-obligations/scripts`.

## Sign-off
- Compliance Officer: ___________________________
- Date: ___________________________
