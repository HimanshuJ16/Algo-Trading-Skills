# Workflows for IRC Section 475(f) MTM Tax Accounting

Scope: US federal income tax, trader in securities. Not tax advice.

## 1. Verify the election is perfected — before trusting any boolean

`is_mtm_elected=True` is an assertion about paperwork, not a strategy setting.
Confirm all of:

- A Rev. Proc. 99-17 statement was filed by the **unextended** due date of the
  return for the year *preceding* the first effective year (new taxpayers: placed
  in books and records within 2 months and 15 days of the start of the election
  year).
- The statement names the first effective taxable year and the trade or business.
- **Form 3115** was filed for the year of change with the § 481(a) adjustment.
- The tax year being computed is not earlier than the first effective year
  (§ 475(f)(3) — the election is never retroactive).

If any of these is unconfirmed, compute the capital branch. Reporting ordinary
Form 4797 income on an unperfected election is the failure mode that costs the
whole benefit on examination.

## 2. Partition the blotter before computing anything

Three buckets, and only the first is marked:

1. **Elected securities** — held in connection with the trading business.
2. **Identified investment securities** (§ 475(f)(1)(B)) — clearly identified in
   the records on or before the day acquired. Excluded from the mark, capital
   character retained, **§ 1091 still applies**. Route these to
   `wash-sale-rule-tracking-us`.
3. **§ 1256 contracts** — reached only by a separate § 475(f)(2) commodities
   election. Without it, route to
   `section-1256-contract-tax-treatment-us-futures` for 60/40 and Form 6781.
   With it, they become ordinary MTM income and forfeit 60/40.

A same-day identification cannot be manufactured at year end. If the records do
not show it, the security is in bucket 1.

## 3. Compute realized and marked P&L

- Realized P&L per closed trade: `(sell_price - adjusted_basis) * quantity`.
  For a lot marked at a prior year end, `adjusted_basis` is **the prior mark**
  (§ 475(a)), not the purchase price.
- Marked P&L per open lot:
  `(year_end_fmv - section_475_basis) * quantity`, where `section_475_basis` is
  the prior year-end mark if the lot was carried across a year end, otherwise the
  purchase price. **Open shorts mirror the sign** — flag them rather than passing
  a negative quantity, which would silently invert the result.
- Set the § 1091 disallowance to $0 for bucket 1 only. Do **not** also waive
  § 1092 straddle deferral — § 475(d)(1) expressly preserves it.

## 4. Apply the loss limitation that actually governs

- **Elected (ordinary, Form 4797 Part II):** test § 461(l) on the taxpayer's
  **aggregate** net business income, not the trading business alone. Excess over
  the threshold is disallowed this year and carried forward as an NOL (80%-of-
  taxable-income limited on use). If no citable threshold exists for the year,
  report "not evaluated" and complete Form 461 — do not report an unlimited
  deduction as if it were the answer.
- **Not elected (capital, Form 8949 / Schedule D):** allow losses against capital
  gains, then the lower of $3,000 ($1,500 MFS) or the excess (§ 1211(b)); carry
  the remainder forward indefinitely (§ 1212(b)).

## 5. Generate the audit report

Emit realized P&L, marked P&L, the disallowance actually applied, the currently
deductible amount, every carryforward, the form mapping, and a structured list of
anything that was routed out, capped, or left unevaluated. A silent exclusion is
the defect that survives review; a logged one does not.
