# Workflows for ASIC DRT Reporting

Rule references are to the ASIC Derivative Transaction Rules (Reporting) 2024 (F2022L01706, as
amended). See `standards.md` for the full rule map, the currency note and the source links.

## 1. Trade execution and capture

The desk executes an OTC derivative in a Prescribed Class — for example a 5-year AUD interest
rate swap, or an FX swap. Capture the **trade date** (the day the Reportable Transaction occurs,
which starts the Rule 2.2.3 clock) and the **Relevant Jurisdiction**: Australia if the
transaction was booked to the P&L of an Australian branch or entered into in Australia,
otherwise the jurisdiction where it was booked or entered into. The Relevant Jurisdiction
determines which public and bank holidays are excluded from the Business Day count.

## 2. Identifier enrichment

- **UTI (Item 1).** Determine the UTI generating entity under Rule 2.2.9(3) Table 2 and obtain
  the UTI. Where the generating entity is another party and the UTI does not arrive in time,
  Rule 2.2.9(6) requires the Reporting Entity to generate and report one, and to report the
  change later under Rule 2.2.2(2)(c) once the real UTI arrives.
- **UPI (Item 2).** Fetch from the ANNA Derivatives Service Bureau. Skip only for a report about
  the *termination* of an OTC Derivative, where Item 2 does not require it.
- **Counterparty identifier (Items 7 and 8).** Use the LEI where the entity has one. Where the
  counterparty is a natural person not eligible for an LEI per the ROC Statement, report the
  Client Code; where the transaction was cleared by a CCP and the counterparties were not
  disclosed to each other, report `ANON`. In both cases set the Item 8 indicator to False.
- **Package identifier (Item 92).** Assign one where the transaction is one of two or more
  reported separately as a single economic arrangement, where it could not be reported as a
  single report, or where an FX swap is reported as two FX contracts with different expiration
  dates.

## 3. Deadline classification

Set the two flags that drive Rule 2.2.3 **before** validating:

| Situation | `requires_package_identifier` | `is_fx_swap_leg` | Deadline |
|---|---|---|---|
| Ordinary Reportable Transaction | False | False | T+2 |
| Leg of a package / multi-report economic arrangement | True | False | T+4 |
| FX contract forming part of an FX swap, reported as two contracts | True | True | **T+2** |

The third row is the trap: Item 92(c) requires a package identifier for precisely the case that
Rule 2.2.3(3) excludes from the T+4 extension. Classifying it as T+4 makes a real T+2 breach
invisible.

## 4. Validation sweep

Run the sweep on the evening of each Business Day, before the T+2 cut-off for the oldest
outstanding trades:

```python
records = AsicDrtReportingEngine().batch_validate(
    trades,
    reporting_date=today_local,          # Relevant-Jurisdiction local date
    holidays=relevant_jurisdiction_holidays,
    repository_unavailable_at_deadline=tr_outage_flag,
)
```

`reporting_date`, each `trade_date` and every element of `holidays` must be plain
`datetime.date` objects in the Relevant Jurisdiction's local calendar; `datetime` values are
rejected with `TypeError`. Item 103 timestamps are UTC and a UTC instant near midnight resolves
to a different local day, and a `datetime` inside the holiday set would never compare equal to
the date being tested — it would be skipped, the deadline overstated, and a genuine late report
left unflagged. A `reporting_date` earlier than the `trade_date` raises `ValueError`, and a
non-string identifier raises `TypeError` rather than being coerced.

## 5. Exception management

- `is_ready_for_reporting = False` → route to the middle-office exception queue. `missing_fields`
  names the failing Table S1.1(1) item, so the queue can be triaged by data element: Item 7
  (counterparty identifier), Item 1 (UTI), Item 2 (UPI), Item 92 (package identifier). Do not
  serialise a failing trade — an invalid identifier is rejected by the repository and the
  Reporting Entity remains in breach of Rule 2.2.3 while it is being corrected.
- `is_late_submission = True` and `repository_outage_relief_may_apply = False` → escalate for
  late-reporting remediation.
- `is_late_submission = True` and `repository_outage_relief_may_apply = True` → Rule 2.2.3(2)
  applies: the obligation is to report as soon as practicable once the repository is available.
  Record the outage window and the time of submission as evidence. Do not self-report this as an
  automatic breach, and equally do not treat the outage as an open-ended extension.

## 6. Serialisation and submission

Trades passing validation are serialised to an ISO 20022 message definition covering the
Part S1.3 Derivative Transaction Information, using that definition's XML tags (Rule 2.2.4(2)),
and submitted to a Licensed or Prescribed Derivative Trade Repository.

## 7. Post-submission obligations not covered by this module

- **Rule 2.2.2** — lifecycle reporting: modifications, terminations, assignments, valuation and
  collateral updates, and a change to the UTI where one was generated under Rule 2.2.9(6). Each
  change carries its own Rule 2.2.3 deadline measured from the day the change occurs.
- **Rule 2.2.5** — continuity of reporting when moving between trade repositories.
- **Completeness against the full Table S1.1(1)**, plus the valuation (S1.1(2)) and collateral
  (S1.1(3)) tables. This engine checks four identifiers and the deadline.
- **LEI currency.** Items 5, 6 and 23 require the *current* LEI; renewal lapses are a common
  source of repository rejections and are invisible to a structural check.
