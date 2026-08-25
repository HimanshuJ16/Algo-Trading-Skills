# Standards for Estimated Tax Payment Scheduling

Jurisdiction: **United States federal, individual taxpayers.** Every rule below is
IRC section 6654 unless stated. Nothing here applies to state estimated tax, to
corporations (section 6655), or to nonresident aliens under section 6072(c).

## What the statute actually requires

| Provision | Requirement | What it does NOT say |
|---|---|---|
| **6654(c)(1)–(2)** | Four required instalments per taxable year, due April 15, June 15, September 15, and January 15 of the following year. | The table is calendar-year. Section 6654(k) substitutes corresponding months for a fiscal-year taxpayer; section 6654(j) gives a nonresident alien only **three** instalments. |
| **6654(d)(1)(A)** | Each required instalment is **25%** of the required annual payment. | It does not permit uneven funding under the regular method. Uneven funding is the separate annualized method, 6654(d)(2). |
| **6654(d)(1)(B)** | Required annual payment is **the lesser of** (i) 90% of the tax shown on the return for the taxable year, or (ii) 100% of the tax shown on the preceding year's return. | "Lesser", not greater. Clause (ii) is unavailable if the preceding year "was not a taxable year of 12 months" or the individual "did not file a return for such preceding taxable year". |
| **6654(d)(1)(C)** | Where prior-year AGI **exceeds $150,000**, clause (ii) applies at **110%** instead of 100%. For a married individual filing a separate return, **$75,000** is substituted for $150,000. | Neither dollar amount is indexed for inflation. The test is on AGI shown on the **prior year** return, and it is strict — exactly at the threshold, 100% still applies. |
| **6654(d)(2)** | Annualized income installment method: 22.5% / 45% / 67.5% / 90% of tax on annualized income through each period. Reported on Form 2210 Schedule AI. | **Not implemented by this skill.** A trader with late-year gains and no prior-year safe harbor needs it; this engine will over-state Q1 and Q2 for them. |
| **6654(e)(1)** | No addition to tax where the tax shown, reduced by the section 31 withholding credit, is **less than $1,000**. | Strict: exactly $1,000 does not qualify. Also not indexed. |
| **6654(e)(2)** | No addition to tax where **all three** hold: the preceding year was a 12-month year, the individual had **no** liability for it, and the individual was a US citizen or resident **throughout** it. | Conjunctive. A prior year of zero tax does not help a taxpayer who was not a US person for all of it. |
| **6654(f)** | "The tax" means chapter 1 + chapter 2 (self-employment) + chapter 2A (net investment income) tax, reduced by credits **other than** the section 31 wage withholding credit. | Withholding is not netted here — it is credited under (g) instead. |
| **6654(g)** | The section 31 withholding credit is deemed a payment of estimated tax, with **an equal part deemed paid on each due date**. | Timing-blind by default: December withholding still counts toward the April instalment. The taxpayer may instead elect to use actual withholding dates. |
| **6654(h)** | No addition for the **4th** instalment if the return is filed and the tax paid in full **on or before January 31** of the following year. | Covers the 4th instalment only. It does nothing for Q1–Q3 underpayments. |
| **6654(a) / 6621 / 6622** | The addition is computed at the section 6621 underpayment rate — the federal short-term rate **plus 3 percentage points** — compounded daily. | The rate is reset quarterly. It was **7%** for individuals for both the third and fourth quarters of 2026. |
| **7503** | Where the last day for performing an act falls on a Saturday, Sunday, or **legal holiday**, the act is timely on the next day that is none of those. "Legal holiday" means a legal holiday **in the District of Columbia**. | This is why DC's Emancipation Day (April 16) moves the Q1 estimated tax deadline nationwide. The IRS applies the rule to estimated tax payments, not just returns. |
| **1411(a), (b), (c)(2)(B)** | 3.8% tax on net investment income above MAGI of $250,000 (joint), $125,000 (separate), $200,000 (others). A trade or business of **trading in financial instruments or commodities** is an applicable trade or business. | Trading income is therefore inside the section 6654(f) base. Omitting it understates the 90% target. |

## Where states diverge — do not reuse the federal schedule

| Source | Rule |
|---|---|
| **Cal. R&TC 19136.1** | For taxable years beginning on or after 2010-01-01, California's required instalments are **30% / 40% / 0% / 30%** — not four equal quarters. A trader funding evenly is short after June 15. |
| **Cal. R&TC 19136.3** | Clause (ii) of IRC 6654(d)(1)(B) "shall not apply if the adjusted gross income shown on the return of the individual **for the taxable year** is equal to or greater than $1 million ($500,000 in the case of a married individual filing a separate return)". Note this tests **current-year** AGI, unlike the federal prior-year test. |

Other states have their own rules. Treat the federal output as one input to a state
calculation, never as the state answer.

## Engine behaviour

| Rule | Behaviour |
|---|---|
| Cent allocation | Cumulative requirements round **up** to the cent, so no instalment is ever reported below its 25% share; the four still sum exactly to the required annual payment. The withholding credit rounds **down**, so it is never over-stated. |
| Rounding mode | Half away from zero. `round()` is avoided because half-to-even can leave a schedule a cent short of the statutory share. |
| Threshold comparison | Strict `>` for the 6654(d)(1)(C) AGI test; strict `<` for the 6654(e)(1) de minimis test. |
| `is_safe_harbor_compliant` | True only when every **evaluated** instalment has zero cumulative shortfall. Instalments are evaluated once their adjusted due date has passed; with no `as_of_date`, all four are evaluated, so an unfunded forward plan reports False rather than True. |
| Shortfall | Cumulative, not per-payment: `cumulative required − (rateable withholding + payments to date)`, floored at zero. A surplus carries forward, per 6654(b)(2). |
| Determinism | No wall-clock reads. `as_of_date` is an explicit argument. |
| Supported years | 2005–2100. The DC holiday calendar used for the 7503 shift is only accurate from 2005 (Emancipation Day) and includes Juneteenth from 2021; outside the range the engine raises rather than emitting a wrong date. |
| Invalid input | Raises `EstimatedTaxError`. Negative amounts, NaN, infinity, unknown filing status, non-integer year, and more than four payment entries are all rejected. |

## Sources

- 26 U.S.C. § 6654, Failure by individual to pay estimated income tax — https://www.law.cornell.edu/uscode/text/26/6654
- 26 U.S.C. § 7503, Time for performance of acts where last day falls on Saturday, Sunday, or legal holiday — https://www.law.cornell.edu/uscode/text/26/7503
- 26 U.S.C. § 1411, Imposition of tax (net investment income tax) — https://www.law.cornell.edu/uscode/text/26/1411
- IRS, *Estimated taxes* (confirms the $1,000 threshold, the lesser-of rule, and the weekend/holiday shift for estimated tax payments) — https://www.irs.gov/businesses/small-businesses-self-employed/estimated-taxes
- IRS Publication 505, *Tax Withholding and Estimated Tax* — https://www.irs.gov/publications/p505
- IRS, *About Form 1040-ES* — https://www.irs.gov/forms-pubs/about-form-1040-es
- IRS, *Interest rates remain the same for the fourth quarter of 2026* (7%, federal short-term plus 3 points, compounded daily) — https://www.irs.gov/newsroom/interest-rates-remain-the-same-for-the-fourth-quarter-of-2026
- IRS, *When to Pay Estimated Tax* FAQ (April 18, 2017 Q1 deadline) — https://www.irs.gov/faqs/estimated-tax/individuals/individuals-2
- Rev. Rul. 2015-13, section 7503 and District of Columbia holidays — https://www.irs.gov/pub/irs-drop/rr-15-13.pdf
- Cal. Rev. & Tax. Code § 19136.1 (30/40/0/30 instalments) — https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml?lawCode=RTC&sectionNum=19136.1
- Cal. Rev. & Tax. Code § 19136.3 ($1 million AGI removes the prior-year safe harbor) — https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml?lawCode=RTC&sectionNum=19136.3
