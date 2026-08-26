# Standards for IRC Section 475(f) Mark-to-Market Tax Accounting (US Federal)

Jurisdiction: **United States federal income tax only.** Several states do not
conform to Sec. 475(f), Sec. 461(l), or the federal NOL rules; state treatment
must be determined separately. This is engineering guidance for building an
audit-support computation, **not tax advice**.

## 1. Statutory rule matrix

| Rule | Authority | Requirement | Engineering effect |
| :--- | :--- | :--- | :--- |
| **Year-end mark** | [26 U.S.C. § 475(f)(1)(A)](https://www.law.cornell.edu/uscode/text/26/475) | Securities held in connection with the trading business are treated as sold at FMV on the last business day of the taxable year. | Mark every non-excluded open lot; do not defer to the sale. |
| **Basis adjustment** | § 475(a), applied via § 475(f)(1)(A) | "Proper adjustment shall be made in the amount of any gain or loss subsequently realized." | A lot marked at a prior 12/31 MUST re-mark from that prior mark, and a subsequent sale MUST use it as basis. Marking from original cost double-counts. |
| **Wash sale waiver (scoped)** | § 475(f)(1)(D) → § 475(d)(1) | "Section 1091 shall not apply (**and section 1092 shall apply**) to any loss recognized under subsection (a)." | Set the § 1091 disallowance to $0 **for elected securities only**. Do NOT waive § 1092 straddle deferral. Do NOT waive § 1091 for securities identified as investments. |
| **Ordinary character** | § 475(d)(3)(A); [IRS Topic No. 429](https://www.irs.gov/taxtopics/tc429) | Marked gain/loss is ordinary, reported on **Form 4797, Part II**. | Never route elected P&L to Form 8949 / Schedule D. |
| **Investment carve-out** | § 475(f)(1)(B) (rules similar to § 475(b)(2)) | A security held other than in connection with the trading business is excluded **only if clearly identified as such in the records** before the close of the day it was acquired. | Identification is a same-day recordkeeping obligation, not a year-end reclassification. Excluded securities stay capital and stay subject to § 1091. |
| **Commodities are a separate election** | § 475(f)(2); § 475(c)(2) (definition of "security") | A § 475(f)(1) securities election does not reach commodities or § 1256 contracts. | Do not sweep futures/broad-based index options into a securities election. |
| **§ 1256 interaction** | § 475(f)(2) → § 475(f)(1)(D) → § 475(d)(1) | § 1256(a) "shall not apply to securities to which subsection (a) applies." | Making the § 475(f)(2) commodities election forfeits 60/40 treatment on § 1256 contracts. |
| **Excess business loss** | [26 U.S.C. § 461(l)(3)(A)](https://www.law.cornell.edu/uscode/text/26/461); [Instructions for Form 461](https://www.irs.gov/instructions/i461) | A noncorporate taxpayer's aggregate net business loss above the threshold is disallowed for the year and carried forward as an NOL. | The ordinary loss deduction is **not** unlimited. Test on aggregate business income, not the trading business alone. |
| **Capital loss limitation (no election)** | [26 U.S.C. § 1211(b)](https://www.law.cornell.edu/uscode/text/26/1211) | Allowed to the extent of capital gains, plus the lower of $3,000 ($1,500 MFS) or the excess. | Not inflation-indexed. Use $1,500 for married filing separately. |
| **Capital loss carryforward** | [26 U.S.C. § 1212(b)](https://www.law.cornell.edu/uscode/text/26/1212) | The unallowed excess carries to the succeeding year, retaining short-term/long-term character. | Report the carryforward; discarding it breaks the following year's return. |
| **Self-employment tax** | IRS Topic No. 429 | "Gains and losses from selling securities from being a trader aren't subject to self-employment tax." | Do not apply SE tax to Form 4797 Part II trading income. |

## 2. Election mechanics (Rev. Proc. 99-17; IRS Topic No. 429)

| Step | Deadline | Detail |
| :--- | :--- | :--- |
| Election statement | Due date of the return for the year **immediately preceding** the first effective year, **without regard to extensions** | Attach to that return or to the extension request. Must state that a § 475(f) election is being made, the first effective taxable year, and the trade or business. |
| New taxpayer (no prior-year filing obligation) | Within **2 months and 15 days** of the first day of the election year | Place the same statement in the books and records. |
| Method change | Return for the year of change (extensions included) | File **Form 3115** under the current automatic-change procedure (Rev. Proc. 2025-23 per Topic No. 429) with the § 481(a) adjustment. Form 3115 implements the election; the Rev. Proc. 99-17 statement makes it. |
| Duration / revocation | § 475(f)(3) | Made separately for each trade or business, without Secretary consent, and applies to the election year and all subsequent years until revoked — revocation requires a notification statement **and** a Form 3115. |

## 3. Threshold amounts

Amounts below are cited, not derived. Do not extrapolate an inflation adjustment
Treasury has not published.

| Provision | Year | Amount | Source |
| :--- | :--- | :--- | :--- |
| § 461(l)(3)(A)(ii)(II) | 2025 | $313,000 ($626,000 joint) | [Instructions for Form 461 (2025)](https://www.irs.gov/instructions/i461) |
| § 461(l)(3)(A)(ii)(II) | 2026 | $256,000 ($512,000 joint) | Rev. Proc. 2025-32, § .31 |
| § 1211(b) | all | $3,000 ($1,500 MFS) | 26 U.S.C. § 1211(b) — statutory, not indexed |

The § 461(l) limitation was made permanent by the One Big Beautiful Bill Act
(2025), which also rebased the inflation adjustment; the 2026 figure above is the
first amount published on that basis. A disallowed excess business loss becomes
an NOL carryover, and post-2017 NOL carryforwards are limited to 80% of taxable
income on use.

## 4. Worked comparison

Trader with $600,000 of net ordinary trading loss, single filer, tax year 2026,
no other business:

| | § 475(f) elected | No election (capital) |
| :--- | ---: | ---: |
| Reportable P&L | $(600,000) ordinary | $(600,000) capital |
| Currently deductible | $(256,000) — § 461(l) | $(3,000) — § 1211(b) |
| Carried forward | $344,000 as NOL (80% limited) | $597,000 capital loss (indefinite) |
| Form | 4797 Part II | 8949 / Schedule D |

The election is decisively better here, but "$600,000 fully deductible this year"
is wrong under both regimes.
