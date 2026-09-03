---
name: double-taxation-treaty-considerations-cross-border-trading
description: Cross-border withholding tax and Foreign Tax Credit audit engine for
  bilateral tax treaties — applies registered treaty rates per income article, and
  separates recoverable credit from tax withheld above an available treaty rate,
  which is generally not creditable.
domain: Tax Accounting & Reporting
subdomain: Cross-Border Tax & Double Taxation Treaties
tags:
- double-taxation
- dtta
- withholding-tax
- wht-reduction
- foreign-tax-credit
- w-8ben-e
- cross-border-trading
brokers_frameworks:
- OECD Model Tax Convention
- IRS Form W-8BEN-E
- IRC s.901 / s.904 Foreign Tax Credit
- Python Dataclasses
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in cross-border trading operations, multi-entity portfolio management, and dividend accounting engines. When an entity resident in Country A holds securities issued in Country B, the source country withholds tax at its domestic rate — 30% on US FDAP income, 25% plus solidarity surcharge in Germany, 35% in Switzerland, 0% on UK dividends paid to non-residents. A double taxation treaty may reduce that rate for a given class of income, and the residence country may credit some of what remains.

This engine computes that arithmetic from rates **you register**, and — the part most cost models get wrong — separates the tax that is genuinely recoverable from the tax that is permanently lost.

## When NOT to Use

- **As a treaty database.** It ships with no rates. Treaty rates vary by income article, by shareholding percentage, and by entity type, and are amended by protocols and by the MLI. An unregistered (residence, source, income type) triple returns `REVIEW_REQUIRED`, not a guess.
- **As an entitlement determination.** Beneficial ownership and limitation-on-benefits articles decide whether an entity *may* claim a treaty rate. Those are legal determinations; the engine assumes the rate you register is available.
- **As a filing position.** Output is decision support for a tax adviser. The credit limitation here is a per-payment approximation, not a jurisdiction's real computation (IRC s.904 works on net foreign-source taxable income per separate category, across the whole year).
- **For residency determination.** Use `multi-jurisdiction-tax-residency-implications` to establish which country is the residence country before using this skill.

## Prerequisites

- Residence country, source country, and **income type** (`EQUITY_DIVIDEND`, `INTEREST`, `ROYALTY`, `SECTION_871M_SWAP`) — the income type selects the treaty article and therefore the rate.
- Registered treaty and/or statutory rates per triple, as decimal fractions (`0.15`, not `15`).
- Documentation status (`has_valid_tax_documentation`).
- Either `resident_country_effective_tax_rate` or an explicit `ftc_limitation_usd`. Neither has a default — a Cayman entity's rate is 0.00, and inventing one would erase the zero-tax-jurisdiction problem this skill exists to surface.

## Workflow

1. **Resolve the rate for the (residence, source, income type) triple.**
   - Treaty registered + valid documentation → treaty rate, status `TREATY_APPLIED`.
   - Treaty registered + missing documentation → statutory rate, status `DOCUMENTATION_MISSING`.
   - No treaty but a registered statutory rate → statutory rate, status `STATUTORY_NO_TREATY`.
   - **Decision point:** neither registered → `REVIEW_REQUIRED` with all amounts `None`. A dividend rate is never borrowed to price interest or a swap, and no universal fallback rate is assumed.
2. **Compute withholding and the saving versus the statutory rate.** All money arithmetic runs in `Decimal` with half-up rounding.
3. **Split recoverable from unrecoverable tax.**
   - **Decision point:** where a treaty rate *was available* but not claimed, the excess withheld is a noncompulsory payment and is generally **not** creditable — the credit is figured at the treaty rate regardless of what was withheld. It must be pursued as a source-country refund claim, or written off. Set `limit_credit_to_treaty_rate=False` only if the residence jurisdiction demonstrably credits the full amount.
   - Apply the residence-country ceiling: `eligible_FTC = min(creditable_tax, limitation)`.
   - Report `non_creditable_wht_usd` — the tax that will never come back.
4. **Audit report.** Returns `DoubleTaxationAuditReport` with the status, every amount, and the required action.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Assuming over-withheld tax is creditable.** The costliest error here. Miss the W-8BEN-E, suffer 30% instead of 15%, and a naive engine credits the whole 30% — reporting the mistake as free. The extra 15% is generally not creditable; recover it from the source country or lose it.
- **Applying a dividend rate to interest or royalties.** Treaties rate each income class under its own article. Under the US–UK treaty, portfolio dividends are 15% while interest is generally exempt at source — a 15% "estimate" on interest is a pure fabrication.
- **Treating 30% as the universal statutory rate.** It is the US FDAP rate. The UK withholds nothing on dividends to non-residents; a 30% assumption would invent a $30,000 liability on a $100,000 UK dividend.
- **Unrecoverable leakage in zero-tax jurisdictions.** A Cayman entity has no residence-country liability to credit against, so source WHT is a 100% permanent cost. Passing a non-zero default residence rate hides exactly this.
- **Section 871(m) derivative surprises.** Total return swaps on US equities can produce dividend equivalents subject to US withholding. Delta-one transactions are in scope; under IRS Notice 2024-44 the rules apply to covered non-delta-one transactions issued on or after 1 January 2027, with the good-faith standard extended for delta-one through 2026.
- **Stale documentation.** A Form W-8BEN-E is valid from signature until the **last day of the third succeeding calendar year** — a form signed 30 Sep 2024 runs to 31 Dec 2027, not to 30 Sep 2027. It also lapses immediately on a change in circumstances, which must be reported within 30 days.
- **Percentage-vs-fraction confusion.** Registering `15` instead of `0.15` would withhold 1500%. Rates outside `[0.0, 1.0]` are rejected at registration.
- **Rounding money in binary floats.** `1.50 * 0.15` is `0.22499999999999998`, so even half-up rounding of the float product gives `0.22` where the exact answer is `0.23`. Cents that do not tie out to a broker statement become reconciliation work.

## Verification

- Instantiate `DoubleTaxationTreatyEngine` and register a UK↔US dividend treaty (statutory 30%, treaty 15%, `Form W-8BEN-E`). Audit a $100,000 dividend with valid documentation: expect $15,000 withheld, $15,000 saved, $15,000 credit.
- Repeat **without** documentation: expect $30,000 withheld but `creditable_foreign_tax_usd == 15_000` and `non_creditable_wht_usd == 15_000` — not a $30,000 credit.
- Register a UK↔US interest article at 0% and verify an `INTEREST` payment withholds nothing while the dividend rate is untouched.
- Audit a `SECTION_871M_SWAP` with only a dividend rate registered and verify `REVIEW_REQUIRED` with `applied_wht_pct is None`.
- Audit a US-resident holder of a UK-source dividend with nothing registered and verify `REVIEW_REQUIRED` rather than an assumed 30%.
- Audit a Cayman (`resident_country_effective_tax_rate=0.0`) holder and verify the entire withholding lands in `non_creditable_wht_usd`.
- Verify `1.50` gross at 15% yields `0.23`, and that registering `treaty_wht_pct=15.0` raises `ValueError`.
- Run `python -m unittest discover -s skills/double-taxation-treaty-considerations-cross-border-trading/scripts`.

## Related Skills

- `multi-jurisdiction-tax-residency-implications`
- `form-1099-b-and-broker-tax-reporting-reconciliation`
- `record-keeping-requirements-for-tax-audit-defense`
