# Standards for Crypto Transaction Tax Lot Tracking

**Jurisdiction: United States federal income tax.** Everything below is US law
and IRS guidance. It is not tax advice, and it is not portable to another
jurisdiction. Rules affecting digital assets have changed repeatedly (the broker
regulations apply to dispositions on or after 2025-01-01, and the identification
relief below is time-limited) — re-verify against the primary sources before
relying on any of it.

## Rules enforced by this module

| Rule | Standard |
|---|---|
| Taxable Swap Treatment | Crypto-to-crypto swaps MUST be treated as dispositions recognizing capital gain or loss, measured against the USD fair market value of what is received. |
| Default Matching Method | Absent an adequate identification, units MUST be treated as disposed of in first-in-first-out order. FIFO is therefore the engine default. |
| Specific Identification | HIFO and LIFO are elections of specific identification and MUST be supported by an identification made **no later than the date and time** of the sale, disposition, or transfer. The engine requires an `identification_reference` for those methods. |
| Wallet-by-Wallet Basis | Basis MUST be determined on a wallet-by-wallet / account-by-account basis for dispositions on or after 2025-01-01, not across a universal pool. Lot inventory is scoped per (wallet, asset). |
| Holding Period | Long-term requires holding **more than one year**, counted from the day after acquisition through the day of disposal. A disposal on the one-year anniversary is short-term. MUST NOT be approximated with a `days_held > 365` comparison, which misclassifies across leap years. |
| Per-Transaction Reporting | Each matched lot MUST be reported as its own row with its own acquisition date, disposal date, proceeds and basis; short-term and long-term rows go to different Parts of Form 8949. A single aggregate short/long flag is not sufficient for a disposal spanning both. |
| Transaction Costs | Costs to effect a disposition MUST reduce the amount realized, and MUST NOT also be capitalized into the basis of an asset received in the same exchange. |

## Verified claims and sources

| Claim | Source |
|---|---|
| "If you exchange virtual currency held as a capital asset for other property, including for goods or for another virtual currency, you will recognize a capital gain or loss." | IRS — FAQs on Virtual Currency Transactions, FAQ 16 — https://www.irs.gov/individuals/international-taxpayers/frequently-asked-questions-on-virtual-currency-transactions |
| Absent specific identification, "the units are deemed to have been sold, exchanged, or otherwise disposed of in chronological order beginning with the earliest unit" (FIFO). | IRS — FAQs on Virtual Currency Transactions, FAQ 41 — https://www.irs.gov/individuals/international-taxpayers/frequently-asked-questions-on-virtual-currency-transactions |
| Specific identification requires identifying the units "no later than the date and time of the sale, disposition, or transfer" on the taxpayer's books and records. | IRS — FAQs on Digital Asset Transactions, FAQ 82 — https://www.irs.gov/individuals/international-taxpayers/frequently-asked-questions-on-digital-asset-transactions |
| §1.1012-1(j)(3)(ii) requires "specifying to the custodial broker, no later than the date and time of the sale, disposition, or transfer, the particular units". Failing an adequate identification, §1.1012-1(j)(3)(i) "treats such units as sold, disposed of, or transferred in order of time from the earliest date on which units of that same digital asset held in the custody of the broker were acquired". | IRS — Notice 2025-7, Internal Revenue Bulletin 2025-05 — https://www.irs.gov/irb/2025-05_IRB (PDF: https://www.irs.gov/pub/irs-drop/n-25-07.pdf) |
| Temporary relief lets a taxpayer make the identification on their own books and records (including via a standing order) instead of with the broker. Notice 2026-20 defines the relief period as "January 1, 2025, and ending on December 31, 2026". | IRS — Notice 2026-20, Internal Revenue Bulletin 2026-15 — https://www.irs.gov/irb/2026-15_IRB (PDF: https://www.irs.gov/pub/irs-drop/n-26-20.pdf) |
| Rev. Proc. 2024-28 provides a safe harbor to allocate unused basis to digital assets held "within each wallet or account of the taxpayer as of January 1, 2025", referencing §1.1012-1(j)(3)(ii)'s requirement that "basis be determined on a wallet-by-wallet or account-by-account basis". | IRS — Rev. Proc. 2024-28, Internal Revenue Bulletin 2024-31 — https://www.irs.gov/irb/2024-31_IRB (PDF: https://www.irs.gov/pub/irs-drop/rp-24-28.pdf) |
| "To figure the holding period, begin counting on the day after you received the property and include the day you disposed of it." Part I is for short-term ("1 year or less"); Part II is for long-term ("more than 1 year"). | IRS — Instructions for Form 8949 — https://www.irs.gov/instructions/i8949 |
| "Enter the details of each transaction on a separate row" with column (b) Date Acquired and column (c) Date Sold or Disposed Of. Column (d): "Enter in column (d) the net proceeds. The net proceeds equal the gross proceeds minus any selling expenses." Column (e) basis includes "the purchase price and any costs of purchase, such as commissions." | IRS — Instructions for Form 8949 — https://www.irs.gov/instructions/i8949 |
| "Digital asset transaction costs means the amounts paid in cash or property (including digital assets) to effect the sale, disposition or acquisition of a digital asset… include transaction fees, transfer taxes, and commissions", and the amount realized is reduced by the transaction costs allocable to the disposition. Costs to effect an exchange are allocable to the digital assets disposed of. | Treas. Reg. §1.1001-7 — https://www.ecfr.gov/current/title-26/chapter-I/subchapter-A/part-1/section-1.1001-7 (mirror consulted: https://www.law.cornell.edu/cfr/text/26/1.1001-7) |
| "If you held the digital assets for more than one year before selling or exchanging" the gain or loss is long-term; the holding period "begins on the day after you acquired" the asset. | IRS — FAQs on Digital Asset Transactions, FAQ 50 — https://www.irs.gov/individuals/international-taxpayers/frequently-asked-questions-on-digital-asset-transactions |

## Explicitly NOT established here

- **Wash sales.** This module applies no wash-sale adjustment, and this file makes
  no claim about whether §1091 reaches digital assets. See `wash-sale-rule-tracking-us`
  and confirm current law before relying on a loss.
- **The February 29 anniversary.** No source consulted settles the holding-period
  boundary for a leap-day acquisition. The module resolves the anniversary to
  March 1 (the later, more conservative boundary) and says so in the code; a
  taxpayer in that one-day window should confirm it with a tax adviser.
- **Anything outside US federal tax**, including state treatment and non-US regimes.
