---
name: estimated-tax-payment-scheduling-for-active-trading-income
description: >-
  Use when a US individual earns trading income with no wage withholding and must
  schedule quarterly instalments under IRC 6654: the required annual payment on the 90%
  current-year or 100%/110% prior-year test, and the four instalment dates.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: tax-accounting-reporting-global
  tags: estimated-taxes, quarterly-tax-schedule, safe-harbor-rules, irs-form-1040-es, active-trader-tax, tax-reserve-allocation, irc-6654
  brokers_frameworks: "IRC Section 6654; IRS Form 1040-ES; IRS Publication 505; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when an **individual** US taxpayer earns trading income that arrives without wage withholding — a proprietary trader on a K-1, an LLC member, an S corporation shareholder, or a sole proprietor running an automated strategy. Trading gains generate no employer withholding, so IRC section 6654 imposes an addition to tax unless the year's liability is funded in four instalments as it is earned.

The engine answers three questions the trading desk actually needs: how much must be funded for the year to reach a safe harbor, how much cash must leave the trading account on each of the four dates, and — given what has already been paid — whether any instalment is currently short.

It is a mechanical statutory computation, not tax advice, and it does not verify the eligibility facts the caller asserts.

## When NOT to Use

- **For state estimated tax.** Section 6654 is federal. Several states diverge in ways that make the federal schedule actively wrong: California requires instalments of 30%, 40%, 0% and 30% (R&TC 19136.1), not four equal quarters, and removes the prior-year safe harbor entirely once current-year AGI reaches $1,000,000 (R&TC 19136.3). Run a separate state calculation.
- **For a C corporation.** Corporate estimated tax is section 6655, with different percentages and its own large-corporation restriction. Only pass-through income landing on Form 1040 is in scope.
- **For a nonresident alien** described in section 6072(c), who has **three** required instalments under section 6654(j), not four.
- **For a fiscal-year taxpayer.** Section 6654(k) substitutes the corresponding months; this engine emits only the calendar-year dates of the section 6654(c)(2) table.
- **When the trader must annualize.** The engine implements the *regular* method only — 25% per instalment. A trader who cannot use the prior-year safe harbor and whose gains land late in the year will be over-scheduled in Q1 and Q2; the annualized income installment method of section 6654(d)(2) and Form 2210 Schedule AI (22.5% / 45% / 67.5% / 90% of annualized tax) is the correct tool there. This engine does not implement it.

## Prerequisites

- **Prior-year figures as shown on the filed return**: `prior_year_agi_usd` (AGI, which is what the section 6654(d)(1)(C) threshold tests) and `prior_year_tax_usd`.
- **`projected_current_year_tax_usd` on the section 6654(f) basis** — chapter 1 income tax **plus** chapter 2 self-employment tax **plus** the chapter 2A net investment income tax, reduced by credits *other than* the section 31 withholding credit. For a trader the NIIT is not optional: section 1411(c)(2)(B) makes a trade or business of trading in financial instruments or commodities an applicable trade or business, so trading gains carry the 3.8% tax above the section 1411(b) MAGI thresholds. Do **not** subtract withholding here.
- **`filing_status`**, because section 6654(d)(1)(C) substitutes a $75,000 AGI threshold for a married individual filing separately.
- **`current_year_withholding_usd`** — wage withholding from any source, including a spouse's W-2 on a joint return.
- **Three eligibility facts** about the preceding year: was it a 12-month year, was a return filed for it, and was the individual a US citizen or resident throughout it.
- **Payments already made, as amounts per instalment** — not a count.

## Workflow

1. **Establish Whether the Prior-Year Limb Is Even Available**: The flush text of section 6654(d)(1)(B) withdraws clause (ii) unless the preceding taxable year was a 12-month year *and* a return was filed for it. A first-year trader has no prior-year safe harbor at all and is thrown onto the 90% current-year figure — which means their required payment moves every time their projection moves. Set `prior_year_return_filed` / `prior_year_was_12_months` honestly; the engine logs a warning and falls back rather than inventing a cheap target.
2. **Pick the Threshold From Filing Status, Then Compare Prior-Year AGI**: $75,000 for married filing separately, $150,000 otherwise. The test is on AGI *shown on the prior year return*, and it is strict — exactly $150,000 does not exceed $150,000, so the multiplier stays at 100%.
3. **Take the Lesser of the Two Limbs**: 90% of projected current-year tax versus 100%/110% of prior-year tax. Section 6654(d)(1)(B) says "the lesser of", which is why a trader coming off a strong year usually funds against the prior-year figure and a trader coming off a weak year funds against the current-year one. `safe_harbor_basis` records which governed.
4. **Split Into Four 25% Instalments and Credit Withholding Rateably**: Section 6654(d)(1)(A) fixes each required instalment at 25% of the required annual payment. Section 6654(g) then deems wage withholding paid in four equal parts *regardless of when it was actually withheld* — so the engine subtracts a quarter of the year's withholding from each instalment and schedules only the residual to Form 1040-ES. Surplus from an early instalment carries forward.
5. **Place Each Instalment on a Payable Date**: The statutory dates are April 15, June 15, September 15 and January 15 of the following year. Section 7503 then moves any that falls on a Saturday, Sunday, or **District of Columbia** legal holiday. Both the raw and the adjusted date are reported, because the adjusted one is the only one that is actually timely.
6. **Measure the Cumulative Shortfall, Not the Single Payment**: The addition to tax runs per instalment on the amount by which cumulative credits fall below the cumulative requirement. Paying the whole year in Q4 does not cure Q1. Pass `as_of_date` explicitly so the report is reproducible and `OVERDUE` means something.
7. **Check the Section 6654(e) Exceptions Before Panicking**: A shortfall does not always mean a penalty. Under (e)(1) there is no addition where tax less withholding is under $1,000; under (e)(2) there is none where the prior year was a 12-month year with no liability and the individual was a US person throughout it. The report flags both — but they suppress the *penalty*, not the tax.
8. **Move the Cash Out of the Trading Account**: Reserve each instalment in cash or T-bills on a date the engine gives you, not in margin equity. Retain the report as the working paper behind the payment.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Applying the $150,000 Threshold to a Separate Return**: Section 6654(d)(1)(C) substitutes $75,000 for a married individual filing separately. A separate filer with $120,000 of prior-year AGI needs 110%, not 100% — a 10% under-funding that looks perfectly compliant on a schedule built from the wrong constant.
- **Claiming the Prior-Year Safe Harbor With No Prior-Year Return**: The cheapest-looking number in a first profitable year is 100% of a prior year in which the trader had no filing requirement. Clause (ii) is simply unavailable then, and the real requirement is 90% of a current year that is still being earned.
- **Omitting the 3.8% NIIT From the Projection**: Trading is a section 1411(c)(2)(B) applicable trade or business. A projection built from the income tax alone understates the 90% target by the whole NIIT, and the shortfall only surfaces at filing.
- **Ignoring Withholding Entirely, or Crediting It When It Was Actually Withheld**: Section 6654(g) deems withholding paid in four equal parts whichever month it happened. A trader who ignores a spouse's W-2 withholding over-funds every quarter; one who assumes December withholding only helps Q4 misses that it retroactively cures earlier instalments — which is exactly why increasing year-end withholding is the standard fix for a Q1 shortfall.
- **Treating a Late Catch-Up as a Cure**: The addition to tax is computed per instalment. Wiring the full annual amount on January 15 leaves Q1, Q2 and Q3 underpaid for the days they were underpaid, and interest runs at the section 6621 rate — the federal short-term rate plus three points, compounded daily (7% for the third and fourth quarters of 2026).
- **Paying on the Statutory Date When Section 7503 Moved It — or Assuming It Never Moves**: April 15, 2028 is a Saturday and DC's Emancipation Day falls that weekend, so Q1 2028 is not due until Tuesday April 18. January 15, 2029 is Martin Luther King Jr. Day, so that Q4 moves to January 16. A hard-coded "April 15" string cannot express either.
- **Reading a $0 Required Payment as $0 Owed**: A prior year with no liability legitimately produces a $0 required annual payment under clause (ii) — and no penalty under (e)(2). The tax on this year's gains is still due in full at filing. The engine schedules nothing; the desk must still reserve the cash.
- **Applying This Federal Schedule to a State**: Four equal quarters is a federal shape. California's is 30/40/0/30, and its front-loading means a trader who funds evenly is already short after June 15.
- **Draining the Reserve Back Into Positions**: Tax reserves parked in margin equity get consumed by a drawdown, and the payment date does not move because the book had a bad month.

## Verification

- Prior-year AGI $200,000, prior-year tax $50,000, projected current-year tax $90,000: confirm 110% is applied, the prior-year limb ($55,000) is selected as the *lesser* of $55,000 and $81,000, and each instalment is $13,750.
- Set prior-year AGI to exactly $150,000 and confirm the multiplier stays at 100%; add one cent and confirm it becomes 110%.
- Filing status `MARRIED_FILING_SEPARATELY` with $120,000 prior-year AGI: confirm the threshold resolves to $75,000 and the multiplier to 110% — the pre-2.0 engine hard-coded $150,000 and returned 100%.
- `prior_year_return_filed=False` with projected tax $120,000: confirm the prior-year limb is unavailable and the requirement is $108,000 — the pre-2.0 engine applied the prior-year limb unconditionally and scheduled $0.
- Add $20,000 of withholding to the $55,000 case and confirm each instalment reports a $5,000 section 6654(g) credit and $8,750 to remit, totalling $35,000 rather than $55,000.
- Pay $13,500 per quarter against a $13,750 requirement and confirm the report is **not** compliant with a $1,000 cumulative shortfall — a count-based tracker sees four payments and reports success. The pre-2.0 engine hard-coded `is_safe_harbor_compliant=True` regardless.
- Pay nothing in Q1–Q3 and $55,000 in Q4 and confirm Q1 still shows a $13,750 shortfall.
- Confirm `apply_section_7503` returns April 18 for April 15, 2017 (the IRS-published deadline) and April 18 for April 15, 2011, and that the 2028 Q4 instalment lands on January 16, 2029 rather than MLK Day.
- Confirm negative amounts, NaN, infinity, an unknown filing status, a non-integer `tax_year`, and more than four payment entries each raise `EstimatedTaxError`.
- Run `python -m unittest discover -s skills/estimated-tax-payment-scheduling-for-active-trading-income/scripts` and confirm a 100% pass rate.

## Related Skills

- `mark-to-market-election-for-active-traders-us`
- `capital-gains-vs-business-income-classification`
- `section-1256-contract-tax-treatment-us-futures`
- `wash-sale-rule-tracking-us`
- `record-keeping-requirements-for-tax-audit-defense`
- `multi-jurisdiction-tax-residency-implications`
