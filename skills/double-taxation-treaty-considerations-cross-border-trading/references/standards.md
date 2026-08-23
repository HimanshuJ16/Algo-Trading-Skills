# Standards for Double Taxation Treaty Considerations

Rules below are jurisdiction-specific. The engine encodes none of these rates —
they are registered by the caller — and this table exists so the registered
values can be sanity-checked against their source.

## Documentation

| Item | Standard |
|---|---|
| Form W-8BEN-E validity | Valid from the date signed until the **last day of the third succeeding calendar year** (a form signed 30 Sep 2024 is valid through 31 Dec 2027), unless a change in circumstances makes the information incorrect. Under certain conditions a Form W-8BEN-E remains in effect indefinitely absent a change of circumstances. |
| Change in circumstances | The account holder must notify the withholding agent, payer, or FFI within **30 days** of the change. |
| Treaty entitlement | Claiming treaty benefits requires beneficial ownership and satisfaction of any limitation-on-benefits article. These are legal determinations, not arithmetic. |

## Withholding rates (illustrative — verify before registering)

| Jurisdiction / instrument | Rate |
|---|---|
| US statutory FDAP (dividends, interest) | 30% |
| US–UK treaty, portfolio dividends (Art. 10) | 15% |
| US–UK treaty, dividends to a company owning ≥10% of voting power (Art. 10) | 5% |
| US–UK treaty, interest (Art. 11) | Generally exempt at source |
| UK dividends paid to non-residents | No withholding tax |
| Germany, domestic dividend WHT | 25% base (Kapitalertragsteuer), plus solidarity surcharge |
| Switzerland, domestic dividend WHT | 35% |
| Singapore, UAE | 0% |

The spread across these rows is the reason the engine refuses to assume a
default: a single hard-coded 30% is right for one jurisdiction and wrong for
most others.

## Foreign Tax Credit

| Item | Standard |
|---|---|
| Noncompulsory payments | Foreign tax a taxpayer was not legally liable to pay is not creditable. Where a lower treaty rate applies, the credit is figured using the **treaty rate**, regardless of the amount actually withheld (Treas. Reg. §1.901-2(e)(5); IRS LB&I practice unit *Reduced Foreign Taxes Under Treaty Provisions*). Over-withheld amounts must be pursued as a source-country refund claim. |
| Credit limitation (US) | IRC §904 caps the credit at US tax liability × (foreign-source **taxable** income ÷ worldwide taxable income), computed **separately for each category/basket** (passive, general, foreign branch, GILTI). Income and taxes cannot be averaged across baskets. |
| Engine approximation | `resident_country_effective_tax_rate × gross income` is a per-payment proxy for the ceiling, not a §904 computation. Supply `ftc_limitation_usd` when the real limitation has been computed. |
| Zero-tax residence | With no residence-country liability, the credit ceiling is zero and all source withholding is a permanent cost. |

## Section 871(m) — dividend equivalents on US equity derivatives

Per IRS Notice 2024-44: the regulations apply to covered **non-delta-one**
transactions issued on or after **1 January 2027**; from that date the scope
extends to derivatives with a delta of 0.8 or higher. The good-faith compliance
standard is extended to delta-one transactions through 2026 and to covered
non-delta-one transactions in 2027, and qualified securities lending transition
rules were extended to payments made in 2025 and 2026.

## Sources

- IRS, *Instructions for Form W-8BEN-E* — https://www.irs.gov/instructions/iw8bene
- IRS, *Instructions for the Requester of Forms W-8BEN, W-8BEN-E, W-8ECI, W-8EXP, and W-8IMY* — https://www.irs.gov/instructions/iw8
- IRS LB&I International Practice Unit, *Reduced Foreign Taxes Under Treaty Provisions* — https://www.irs.gov/pub/fatca/int_practice_units/reduced-foreign-taxes-under-treaty.pdf
- IRS LB&I International Practice Unit, *Categorization of Income and Taxes into Proper Basket* — https://www.irs.gov/pub/fatca/int_practice_units/ftc-categorization-into-proper-basket.pdf
- IRC §904 (Limitation on Credit) — https://www.taxnotes.com/research/federal/usc26/904
- US Treasury, *Technical Explanation of the US–UK Income Tax Convention* — https://www.irs.gov/pub/irs-trty/uktech.pdf
- Convention with Great Britain and Northern Ireland regarding Double Taxation (Treaty Doc. 107-19) — https://www.congress.gov/treaty-document/107th-congress/19/document-text
- IRS Notice 2024-44 coverage, *Section 871(m) dividend equivalent rules phase-in period further extended* (PwC) — https://www.pwc.com/us/en/services/tax/library/section-871m-dividend-equivalent-rules-phasein-period-extended-2.html
- EY Tax News, *IRS provides further transitional relief under IRC Section 871(m)* — https://taxnews.ey.com/news/2024-1100-irs-provides-further-transitional-relief-under-irc-section-871m-for-treatment-of-dividend-equivalents
