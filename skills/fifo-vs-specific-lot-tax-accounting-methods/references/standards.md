# Standards for Tax Lot Accounting Methods

**Jurisdiction: United States federal income tax.** Everything below is US law
and IRS guidance. It is not tax advice. Do not port these rules to another
regime — see "Other jurisdictions" at the end for why the differences are
structural, not parametric.

## Rules enforced by this module

| Rule | Standard |
|---|---|
| Default Matching Method | Absent an adequate identification, a sale MUST be charged against the earliest lot acquired (FIFO). FIFO is therefore the engine default and is the only strategy that requires no identification record. |
| Specific Identification | LIFO, HIFO and explicit lot designation are all elections of specific identification. Each MUST be supported by an identification made no later than the **earlier of the settlement date or the Rule 15c6-1 settlement time**. A standing order or instruction qualifies. The engine requires an `identification_reference` for all three. |
| No Spill Beyond Designation | A `SPECIFIC_LOT` sale MUST consume only the designated lots, in the designated order. If the designation does not cover the sale quantity the engine raises; it MUST NOT silently deliver undesignated shares. |
| Holding Period | Long-term requires holding **more than one year**, counted from the day after acquisition through the day of disposition. A sale on the one-year anniversary is short-term. MUST NOT be approximated with a `days_held > 365` comparison, which misclassifies across leap years. |
| Holding Period Is Not a Lot Attribute | The term MUST be derived from the acquisition date and the sale date at match time. A `holding_period_days` value stored on the lot is stale for every later sale. |
| Per-Transaction Reporting | Each matched lot MUST be reported as its own row with its own acquisition date, sale date, proceeds and basis; short-term and long-term rows go to different Parts of Form 8949. A single aggregate term flag is not sufficient for a sale spanning both. |
| Lot Accounting Balance | Matched lot quantities MUST sum exactly to the sale quantity, and MUST be drawn only from lots of the same security acquired on or before the sale date. |
| Average Basis Is Not General | Average basis MUST NOT be applied to ordinary corporate stock. It is available only for RIC shares and post-2010 DRP shares held with a custodian, and is not implemented here. |

## Verified claims and sources

| Claim | Source |
|---|---|
| Where shares are sold and "the lot from which it is sold cannot be adequately identified", the stock sold "is charged against the earliest lot the taxpayer purchased or acquired" — the FIFO default. | Treas. Reg. §1.1012-1(c)(1) — https://www.law.cornell.edu/cfr/text/26/1.1012-1 |
| For stock left with a broker or custodian, an adequate identification is made if the taxpayer specifies to the broker the particular stock to be sold and, "within a reasonable time thereafter, confirmation of such specification is set forth in a written document from such broker". | Treas. Reg. §1.1012-1(c)(3) — https://www.law.cornell.edu/cfr/text/26/1.1012-1 |
| "For purposes of this paragraph (c), an adequate identification of stock is made at the time of sale, transfer, delivery, or distribution if the identification is made no later than the earlier of the settlement date or the time for settlement required by Rule 15c6-1 under the Securities Exchange Act of 1934, 17 CFR 240.15c6-1 (or its successor). A standing order or instruction for the specific identification of stock is treated as an adequate identification made at the time of sale, transfer, delivery, or distribution." | Treas. Reg. §1.1012-1(c)(8) — https://www.law.cornell.edu/cfr/text/26/1.1012-1 |
| Paragraph (c)(8) and its neighbours "apply for taxable years beginning after October 18, 2010." | Treas. Reg. §1.1012-1(c)(11) — https://www.law.cornell.edu/cfr/text/26/1.1012-1 |
| The standard settlement cycle under Rule 15c6-1 was shortened from T+2 to T+1, with a compliance date of **May 28, 2024** — so the identification window under (c)(8) is now approximately one business day. | SEC — Shortening the Securities Transaction Settlement Cycle (Release 34-96930) — https://www.sec.gov/files/rules/final/2023/34-96930.pdf; SEC T+1 FAQ — https://www.sec.gov/exams/educationhelpguidesfaqs/t1-faq |
| Average basis is permitted only for shares in a regulated investment company, or shares acquired after December 31, 2010 in a dividend reinvestment plan, in each case held with a custodian or agent. | Treas. Reg. §1.1012-1(e) — https://www.law.cornell.edu/cfr/text/26/1.1012-1 |
| "To figure the holding period, begin counting on the day after you received the property and include the day you disposed of it." Part I is short-term ("1 year or less"); Part II is long-term ("more than 1 year"). | IRS — Instructions for Form 8949 — https://www.irs.gov/instructions/i8949 |
| "Enter the details of each transaction on a separate row" (subject to the stated aggregation exceptions), with column (b) Date Acquired and column (c) Date Sold or Disposed Of. | IRS — Instructions for Form 8949 — https://www.irs.gov/instructions/i8949 |
| "The holding period of a capital asset begins to run on the day following the date of acquisition"; the period runs by calendar months, so an asset acquired on the last day of a month must not be disposed of before the first day of the month after the period ends. (Illustrated for the then-6-month period: an asset "acquired on April 30, 1963, must not have been disposed of before November 1, 1963".) | Rev. Rul. 66-7, 1966-1 C.B. 188 — https://www.taxnotes.com/research/federal/irs-guidance/revenue-rulings/rev-rul-66-7/d4d1 (mirror consulted: https://www.timbertax.org/research/revenuerulings/capitalgain/66-7/) |
| "The holding period for short-term capital gains and losses is generally 1 year or less. The holding period for long-term capital gains and losses is generally more than 1 year." | IRS — Instructions for Schedule D (Form 1040) — https://www.irs.gov/instructions/i1040sd |
| To identify shares sold, the taxpayer should specifically identify to the broker the particular stock or bond and obtain written confirmation of the identification; where shares cannot be adequately identified, FIFO applies. | IRS — Publication 550, *Investment Income and Expenses*, "Identifying stock or bonds sold" — https://www.irs.gov/publications/p550 |

### Other jurisdictions — why this engine does not port

| Jurisdiction | Why FIFO/LIFO/HIFO/Specific-ID does not map | Source |
|---|---|---|
| United Kingdom | Disposals are matched by statute, with no taxpayer election: acquisitions on the **same day** first (TCGA 1992 s.105(1)), then acquisitions in the **following 30 days** (s.106A(5), (5A)), then the pooled **Section 104 holding**. There is no lot to designate. | HMRC Capital Gains Manual CG51560 — https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg51560 |
| India | For securities held in dematerialised form, s.45(2A) of the Income-tax Act fixes **FIFO** for determining the date of transfer and period of holding; there is no specific-identification election. The long-term threshold for listed securities is **12 months**, not "one year" applied to every asset. | CBDT Circular No. 768 (24-6-1998) — https://www.incometaxindia.gov.in/w/768-circular-no.-768-dated-24-6-1998-1; CBDT Circular No. 704 (28-4-1995) — https://www.incometaxindia.gov.in/w/704-circular-no.-704-dated-28-4-1995 |

## Explicitly NOT established here

- **Wash sales.** This module applies no §1091 adjustment and makes no claim
  about which replacement purchases trigger one. Feed it pre-adjusted basis; see
  `wash-sale-rule-tracking-us`.
- **Corporate actions.** Splits, spin-offs, return of capital and merger
  consideration all change basis and sometimes quantity. Out of scope.
- **The February 29 anniversary.** No source consulted settles the holding-period
  boundary for a leap-day acquisition. The module resolves the anniversary to
  March 1 (the later, more conservative boundary), matching
  `crypto-transaction-tax-lot-tracking`. A taxpayer in that one-day window should
  confirm it with a tax adviser.
- **Which method is optimal.** HIFO minimises the realized gain, not the tax. The
  engine ranks lots; it does not advise.
- **Non-equity instruments.** §1256 contracts (see
  `section-1256-contract-tax-treatment-us-futures`), debt with OID, and
  collectibles have their own regimes.
- **The India sources above.** The two CBDT circular pages returned HTTP 403 to
  automated retrieval during this audit; the citations are recorded from the
  income-tax department's own index. Verify the text directly before relying on
  it.
