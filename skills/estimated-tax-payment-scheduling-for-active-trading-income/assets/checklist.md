# Pre-Flight Checklist

US federal, individual, calendar-year, IRC section 6654 regular installment method.

## Inputs

- [ ] Are prior-year AGI and prior-year tax taken from the **return as filed**, not from an estimate?
- [ ] Is the current-year projection on the **section 6654(f)** basis — income tax **plus** self-employment tax **plus** the 3.8% net investment income tax on trading gains?
- [ ] Has withholding been kept **out** of the tax projection and supplied separately?
- [ ] Is **all** wage withholding captured, including a spouse's on a joint return?
- [ ] Are payments already made recorded as **amounts per instalment**, not a count?

## Safe harbor determination

- [ ] Was the preceding year a **12-month year** with a **return filed**? If not, the prior-year limb is unavailable and 90% of the current year governs alone.
- [ ] Is the AGI threshold **$75,000** because the filing status is married filing separately, rather than the default $150,000?
- [ ] Is the AGI comparison strict — exactly at the threshold still means 100%?
- [ ] Was the **lesser** of the two limbs selected, and is it recorded which one governed?

## Schedule

- [ ] Is each required instalment 25% of the required annual payment?
- [ ] Is withholding credited **one quarter to each due date** under section 6654(g), regardless of when it was actually withheld?
- [ ] Has section 7503 been applied — is any due date falling on a Saturday, Sunday, or **DC** legal holiday (Emancipation Day for Q1, Martin Luther King Jr. Day for Q4) advanced to the next business day?
- [ ] Do the four instalments sum exactly to the required annual payment, with no instalment below its 25% share?

## Funding and monitoring

- [ ] Are reserves held in cash or T-bills, **outside** margin equity?
- [ ] Does cash clear **before** each payable date rather than on it?
- [ ] Is the report evaluated with an explicit `as_of_date` so it is reproducible?
- [ ] Is the **cumulative** shortfall reviewed per instalment, understanding that a late catch-up does not cure an earlier quarter?
- [ ] If an early instalment is short, has increased **year-end withholding** been considered as the remedy that spreads back across all four dates?
- [ ] Have the section 6654(e) exceptions been checked — under $1,000 net of withholding, or a zero-liability 12-month prior year — before assuming a penalty?

## Scope

- [ ] Is a **separate state** calculation being run? (California is 30/40/0/30 and drops the prior-year safe harbor at $1 million of current-year AGI.)
- [ ] Is the taxpayer an individual — not a C corporation (section 6655), a nonresident alien (three instalments under 6654(j)), or a fiscal-year filer (6654(k))?
- [ ] Are gains concentrated late in the year with no prior-year safe harbor available? If so, the annualized method (Form 2210 Schedule AI) is needed — this engine over-states Q1 and Q2.
- [ ] Is a $0 required payment understood as "no instalments required", **not** "no tax owed at filing"?
- [ ] Is the `EstimatedTaxScheduleReport` retained as the working paper behind each payment?
