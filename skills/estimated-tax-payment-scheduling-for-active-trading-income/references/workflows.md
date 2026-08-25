# Workflows for Estimated Tax Payment Scheduling

US federal, individual, calendar-year, regular installment method (IRC section 6654).
See `standards.md` for the statutory basis and for what is out of scope.

## 1. Assemble the inputs on the right basis

1. Pull **prior-year AGI and prior-year tax from the return as filed**, not from an
   estimate or a projection. The section 6654(d)(1)(C) threshold tests AGI shown on
   that return.
2. Build `projected_current_year_tax_usd` on the **section 6654(f)** basis:
   chapter 1 income tax, plus chapter 2 self-employment tax, plus the chapter 2A
   net investment income tax, less credits **other than** section 31 withholding.
   For a trading book the NIIT component is mandatory — trading is a section
   1411(c)(2)(B) applicable trade or business.
3. Collect **all** wage withholding for the year separately, including a spouse's
   on a joint return. Do not net it into the projection.
4. Record three facts about the preceding year: 12-month year, return filed, US
   person throughout. These gate the prior-year limb and the (e)(2) exception.
5. Record payments already made **as amounts per instalment**. A count cannot
   detect a short payment, which is precisely what triggers the addition to tax.

## 2. Determine the required annual payment

1. If the preceding year was not a 12-month year, **or** no return was filed for it,
   the prior-year limb is gone (flush text of section 6654(d)(1)(B)). The
   requirement is 90% of projected current-year tax, and it moves whenever the
   projection moves — re-run the schedule each time the book's expected P&L shifts
   materially.
2. Otherwise resolve the AGI threshold from filing status: **$75,000** for married
   filing separately, **$150,000** otherwise. Compare prior-year AGI strictly:
   exceeding the threshold gives 110%, meeting it exactly gives 100%.
3. Take the **lesser** of the 90% current-year figure and the 100%/110% prior-year
   figure. Record which limb governed.

## 3. Build the instalment schedule

1. Each required instalment is 25% of the required annual payment
   (section 6654(d)(1)(A)).
2. Credit **one quarter of the year's withholding to each instalment**
   (section 6654(g)), regardless of when it was actually withheld. Schedule only the
   residual to Form 1040-ES. Where withholding exceeds an instalment's requirement,
   the surplus carries forward.
3. Place each instalment on its statutory date — April 15, June 15, September 15,
   January 15 — then apply **section 7503**: advance past any Saturday, Sunday, or
   District of Columbia legal holiday. Publish the adjusted date as the payable one
   and keep the statutory date for the working paper.

## 4. Fund the reserve

1. Move each instalment into cash or T-bills on a schedule that clears **before**
   the payable date, not on it.
2. Hold the reserve outside margin equity. A reserve carried as buying power is
   consumed by a drawdown, and the payment date does not move because the book had
   a bad month.
3. Re-run the schedule after any event that changes the projection — a large
   realized gain, a section 475 election, a change in filing status.

## 5. Monitor and remediate

1. Evaluate with an explicit `as_of_date`. Read the **cumulative** shortfall on each
   instalment, not the single payment: the addition to tax runs per instalment for
   the days it was underpaid, so a late catch-up does not cure an earlier quarter.
2. If an early instalment is short, the standard remedy is **increased year-end wage
   withholding** where any exists, because section 6654(g) spreads it back across all
   four due dates. An extra estimated payment, by contrast, is credited when made.
3. Before treating a shortfall as a penalty, check the section 6654(e) exceptions —
   under $1,000 net of withholding, or a zero-liability 12-month prior year with US
   person status throughout. Both suppress the addition to tax; neither reduces the
   tax itself.
4. Section 6654(h) removes the 4th-instalment addition if the return is filed and
   paid in full by **January 31**. Useful for a trader with a large Q4 gain; it does
   nothing for Q1–Q3.

## 6. Escalate out of this engine when it no longer fits

- Gains concentrated late in the year with no prior-year safe harbor → the
  annualized income installment method, section 6654(d)(2) / Form 2210 Schedule AI.
  This engine over-states Q1 and Q2 in that case.
- Any state liability → a separate state schedule. California alone is 30/40/0/30
  and withdraws the prior-year safe harbor at $1 million of current-year AGI.
- A C corporation, a nonresident alien, or a fiscal-year taxpayer → sections 6655,
  6654(j), and 6654(k) respectively.

## 7. Retain the report

Persist each `EstimatedTaxScheduleReport` — the limb selected, both targets, the
threshold applied, the withholding credit, the payable dates, and the cumulative
shortfall per instalment — as the working paper behind each payment. It is what
supports a reasonable-cause position or a Form 2210 computation later.
