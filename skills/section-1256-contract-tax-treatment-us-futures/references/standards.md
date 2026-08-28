# Standards for IRC Section 1256 Contract Tax Treatment (US Federal)

Jurisdiction: **United States federal income tax only.** State conformity to the
capital loss carryback and carryforward rules varies and must be determined
separately. This is engineering guidance for building an audit-support
computation, **not tax advice**.

Sources are the Internal Revenue Code (26 U.S.C.) and the IRS forms and
publications named. Where a proposition comes from the Instructions for Form 6781
rather than the statute, it is labelled as such.

## 1. Statutory rule matrix

| Rule | Authority | Requirement | Engineering effect |
| :--- | :--- | :--- | :--- |
| **Year-end mark** | [26 U.S.C. § 1256(a)(1)](https://www.law.cornell.edu/uscode/text/26/1256) | "each section 1256 contract held by the taxpayer at the close of the taxable year shall be treated as sold for its fair market value on the **last business day** of such taxable year (and any gain or loss shall be taken into account for the taxable year)". | Mark every open contract. The date is the last **business** day, not necessarily December 31. Do not defer to disposal. |
| **Basis adjustment** | § 1256(a)(2) | "proper adjustment shall be made in the amount of any gain or loss subsequently realized for gain or loss taken into account by reason of paragraph (1)". | A contract carried across a year end must have the prior year's mark removed from this year's figure. Reporting inception-to-date P&L again double-counts. |
| **60/40 character** | § 1256(a)(3) | "short-term capital gain or loss, to the extent of 40 percent" and "long-term capital gain or loss, to the extent of 60 percent". | Split every net Part I figure 40/60. Applies to losses exactly as to gains. |
| **Holding period is irrelevant** | [Instructions for Form 6781](https://www.irs.gov/pub/irs-pdf/f6781.pdf), *Mark-to-Market Rules* | Gains and losses are "treated as 60% long term and 40% short term, regardless of how long the contracts were held". | Never compute a holding period for a § 1256 contract. |
| **Wash sales do not apply** | Instructions for Form 6781, *Mark-to-Market Rules* | "The wash sale rules don't apply." | Do not run § 1091 matching over § 1256 contracts. See `wash-sale-rule-tracking-us` for everything else. |
| **All-§1256 straddles only** | § 1256(a)(4) | § 1092 and § 263(g) are turned off "if all the offsetting positions making up any straddle consist of section 1256 contracts". | A **mixed** straddle is not sheltered. Route mixed-straddle legs out of Part I. |
| **Qualifying contracts** | § 1256(b)(1) | Regulated futures contract, foreign currency contract, nonequity option, dealer equity option, dealer securities futures contract. | Exactly five types. Membership is asserted by the caller, never inferred from a symbol. |
| **Exclusions** | § 1256(b)(2) | Not a § 1256 contract: "any securities futures contract or option on such a contract unless such contract or option is a dealer securities futures contract", or "any interest rate swap, currency swap, basis swap, interest rate cap, interest rate floor, commodity swap, equity swap, equity index swap, credit default swap, or similar agreement". | Single-stock futures and every listed swap type are out. Report them, do not silently drop them. |
| **Nonequity vs equity option** | § 1256(g)(3), (g)(6); [Pub. 550](https://www.irs.gov/publications/p550) | A nonequity option is "any listed option which is not an equity option". An equity option is an option on stock "or the value of which is determined directly or indirectly by reference to any stock or any **narrow-based security index**". Pub. 550 describes nonequity options as including broad-based stock index options. | Broad-based index options (SPX, NDX, RUT, VIX) qualify. Narrow-based index options do not. An ETF share is stock, so an option on SPY/QQQ/IWM is an equity option. |
| **Dealer-only types** | § 1256(g)(4), (g)(9) | A dealer equity option must be "purchased or granted by such options dealer in the normal course of his activity of dealing in options"; the dealer securities futures contract parallels it. | Not available to a non-dealer. Flag their use. |
| **Hedging transactions** | § 1256(e); Instructions for Form 6781, line 4 | "The mark-to-market rules don't apply if you properly and timely identified a section 1256 contract as a hedge." Identification is required "before the close of the day on which such transaction was entered into". "The gain or loss on a hedging transaction is treated as ordinary income or loss." | Exclude identified hedges from the mark and from 60/40; they belong in the line 4 adjustment as ordinary. Identification is same-day, not retrospective. |
| **Loss carryback** | [26 U.S.C. § 1212(c)](https://www.law.cornell.edu/uscode/text/26/1212); Form 6781 box D | A taxpayer other than a corporation may elect to carry a net section 1256 contracts loss back 3 years; "40 percent of the amount so allowed shall be treated as a short-term capital loss ... and 60 percent ... as a long-term capital loss". "This subsection shall not apply to any estate or trust." | A § 1256 loss is deferred, not forfeited. Character survives the carryback. Estates, trusts and corporations cannot elect. |
| **Capital loss limitation** | [26 U.S.C. § 1211(b)](https://www.law.cornell.edu/uscode/text/26/1211) | Allowed to the extent of capital gains, plus the lower of $3,000 ($1,500 married filing separately) or the excess. | Not inflation-indexed. Apply other capital gains **before** the cap. |
| **Capital loss carryforward** | [26 U.S.C. § 1212(b)](https://www.law.cornell.edu/uscode/text/26/1212) | The unallowed excess carries to the succeeding year, retaining character. | Report it; discarding it breaks next year's return. |
| **Net investment income tax** | [26 U.S.C. § 1411(c)(1)(A)(ii)](https://www.law.cornell.edu/uscode/text/26/1411); [26 C.F.R. § 1.1411-4](https://www.law.cornell.edu/cfr/text/26/1.1411-4) | Gain from marking to market under § 1256, and realized gain on property held in the trade or business of trading in financial instruments or commodities, is net investment income. | The 3.8% NIIT can sit on top of the capital rates above the MAGI threshold. It is character-blind, so it does **not** change the 60/40 saving. |
| **Self-employment tax** | § 1402(i); Instructions for Form 6781 | "Options and commodities dealers must take any gain or loss from the trading of section 1256 contracts into account in figuring net earnings subject to self-employment tax." | Applies to **dealers**. Do not apply SE tax to a non-dealer trader's § 1256 P&L. |

## 2. Form 6781 Part I line map (2025 form)

| Line | Content | Destination |
| :--- | :--- | :--- |
| 1 | Each § 1256 contract open at year end or closed during the year. Form 1099-B **box 11** aggregates the broker-reported amount. | — |
| 2–3 | Totals and net gain or loss. | — |
| 4 | Form 1099-B adjustments: the § 1256 part of a mixed straddle, the straddle loss reduction, and **the § 1256 part of a hedging transaction (ordinary)**. Attach a statement. | — |
| 5 | Lines 3 + 4. Partnerships route this to Form 1065 Sch. K line 11; S corporations to Form 1120-S Sch. K line 10, and **leave lines 6–9 blank**. | — |
| 6 | Net § 1256 contracts loss carried back, **as a positive number**, only if box D is checked. | Form 1045 or amended return |
| 7 | Lines 5 + 6. | — |
| 8 | Line 7 × **40%** — short-term. | Schedule D (Form 1040) **line 4** |
| 9 | Line 7 × **60%** — long-term. | Schedule D (Form 1040) **line 11** |

Election boxes: **A** mixed straddle election (§ 1256(d)); **B** straddle-by-straddle
identification; **C** mixed straddle account; **D** net section 1256 contracts loss
election. Only box D is modelled by this skill's script.

## 3. Box D mechanics (Instructions for Form 6781)

The **net section 1256 contracts loss** is the *smaller* of:

1. the excess of § 1256 losses over § 1256 gains plus $3,000 ($1,500 married
   filing separately); and
2. the total short-term and long-term capital loss carryovers to the following
   year computed as if line 6 were zero.

The amount reaching any single carryback year is limited to the *smaller* of that
year's Schedule D line 16 gain counting only § 1256 gains and losses, or its
actual Schedule D line 16 gain — both figured before any carryback. The loss goes
to the **earliest year first**, and only "to the extent it doesn't increase or
produce a net operating loss for the carryback year". Carry it back by filing
Form 1045 or an amended return with an amended Form 6781 and Schedule D; on the
prior year's amended Form 6781 the carryback is reported on **line 1**.

The reference script models prong 1 and an aggregate prior-gain ceiling. Prong 2,
the per-year caps and the NOL limit require the full prior-year Schedule D and
must be verified by hand — the script warns whenever a carryback is computed.

## 4. Rates used as defaults

Rates below are cited, not derived. Pass the taxpayer's actual marginal rates for
anything other than a worst-case estimate.

| Rate | Year | Amount | Source |
| :--- | :--- | :--- | :--- |
| Top ordinary / short-term | 2026 | 37% (single incomes above $640,600; $768,700 joint) | [IRS, tax year 2026 inflation adjustments](https://www.irs.gov/newsroom/irs-releases-tax-inflation-adjustments-for-tax-year-2026-including-amendments-from-the-one-big-beautiful-bill) (Rev. Proc. 2025-32) |
| Top adjusted net capital gain | — | 20% | 26 U.S.C. § 1(h)(1)(D) |
| Net investment income tax | — | 3.8% | 26 U.S.C. § 1411(a) |

Blended § 1256 maximum, capital rates only: `0.60 × 20% + 0.40 × 37% = 26.8%`.
With NIIT: `0.60 × 23.8% + 0.40 × 40.8% = 30.6%`. The advantage over
all-short-term treatment is `0.60 × (37% − 20%) = 10.2` percentage points either
way, because NIIT does not depend on character.

## 5. Worked comparison — a $50,000 net § 1256 loss

Single filer, top rates, $30,000 of net § 1256 gain in the 3 preceding years:

| | Modelled as "$3,000 and the rest is gone" | Correct § 1212(c) → § 1211(b) → § 1212(b) |
| :--- | ---: | ---: |
| Carried back (box D) | $0 | $30,000 @ 26.8% = $8,040 |
| Deducted this year | $3,000 @ 37% = $1,110 | $3,000 @ 37% = $1,110 |
| Carried forward | $0 | $17,000, indefinitely |
| **Benefit recognised** | **$1,110** | **$9,150** |

The naive model understates the value of the loss by a factor of eight and would
push a taxpayer toward the wrong year-end decision.
