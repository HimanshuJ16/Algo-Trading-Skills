# Standards for Forex Currency Gain/Loss Tax Treatment

**Jurisdiction: United States federal income tax only. Not tax advice.**
Every rule below is statutory or regulatory; the engine's *parameters* (rates,
filing status, prior-year gains) are taxpayer facts, not standards.

## Statutory authorities

| Authority | What it provides | Citation |
|---|---|---|
| Default character | "[A]ny foreign currency gain or loss attributable to a section 988 transaction shall be computed separately and treated as ordinary income or loss." | [26 U.S.C. § 988(a)(1)(A)](https://www.law.cornell.edu/uscode/text/26/988) |
| Capital election | Election to treat gain/loss "attributable to a forward contract, a futures contract, or option described in subsection (c)(1)(B)(iii) which is a capital asset ... and which is not a part of a straddle as capital gain or loss." **Yields capital character, not 60/40.** | 26 U.S.C. § 988(a)(1)(B) |
| Election scope | The eligible transactions are "[e]ntering into or acquiring any forward contract, futures contract, option, or similar financial instrument." **Spot transactions are outside this clause.** | 26 U.S.C. § 988(c)(1)(B)(iii) |
| Election mechanics | Identify the transaction in books and records "on the date the transaction is entered into"; the method "must be consistently applied"; a verification statement must be attached to the return. **Per transaction, not annual.** | [26 C.F.R. § 1.988-3(b)(3), (b)(4)](https://www.law.cornell.edu/cfr/text/26/1.988-3) |
| RFC carve-out and reverse election | Clause (iii) "shall not apply to any regulated futures contract or nonequity option which would be marked to market under section 1256"; the taxpayer "may elect to have clause (i) not apply" — i.e. may elect *into* § 988 ordinary treatment. | 26 U.S.C. § 988(c)(1)(D)(i), (ii) |
| 60/40 split | "short-term capital gain or loss, to the extent of 40 percent ... and long-term capital gain or loss, to the extent of 60 percent." | [26 U.S.C. § 1256(a)(3)](https://www.law.cornell.edu/uscode/text/26/1256) |
| Mark-to-market | § 1256 contracts held at year end are treated as sold at fair market value on the last business day. | 26 U.S.C. § 1256(a)(1) |
| Contract definition | "foreign currency contract" means a contract "(i) which requires delivery of, or the settlement of which depends on the value of, a foreign currency which is a currency in which positions are also traded through regulated futures contracts, (ii) which is traded in the interbank market, and (iii) which is entered into at arm's length at a price determined by reference to the price in the interbank market." | 26 U.S.C. § 1256(g)(2)(A) |
| Loss carryback | A net § 1256 contracts loss "shall be a carryback to each of the 3 taxable years preceding the loss year," carried "to the earliest of the taxable years," limited to "the net section 1256 contract gain for such year," with 40%/60% character preserved. Elective. | [26 U.S.C. § 1212(c)](https://www.law.cornell.edu/uscode/text/26/1212) |
| Capital loss limit | Losses allowed "only to the extent of the gains from such sales or exchanges, plus (if such losses exceed such gains) the lower of— (1) $3,000 ($1,500 in the case of a married individual filing a separate return), or (2) the excess of such losses over such gains." | [26 U.S.C. § 1211(b)](https://www.law.cornell.edu/uscode/text/26/1211) |
| Carryforward | Unused capital loss carries to the succeeding taxable year, indefinitely for non-corporate taxpayers. | 26 U.S.C. § 1212(b) |
| Reporting | Form 6781, *Gains and Losses From Section 1256 Contracts and Straddles*: Part I marks § 1256 contracts to market; **box D "Net section 1256 contracts loss election"** with the carryback amount on line 6; lines 8 and 9 apply the 40%/60% split. | [IRS Form 6781 (2025)](https://www.irs.gov/pub/irs-access/f6781_accessible.pdf) |

## Unsettled: § 1256 eligibility for retail forex

- *Wright v. Commissioner*, 809 F.3d 877 (6th Cir. 2016) reversed the Tax Court and held that an over-the-counter option on a major currency could be a "foreign currency contract" under the plain text of § 1256(g)(2)(A)(i).
- Proposed regulations **REG-130675-17**, 87 FR 40224 (July 6, 2022), would define "foreign currency contract" to include **only foreign currency forward contracts**, excluding options, and would expressly overrule *Wright*. Proposed applicability: contracts entered into on or after 30 days after final publication.
- **Status as of this writing: proposed. Finalization was not verified.** Treat § 1256 eligibility for any non-exchange-traded currency position as a determination requiring professional advice, not a default.

## Rate parameters (taxpayer facts, not standards)

For tax year 2026 the top federal ordinary rate remains **37%**, the top
long-term capital gains rate is **20%**, and short-term capital gain is taxed at
ordinary rates. Blended § 1256 rate at those inputs: 0.60 × 20% + 0.40 × 37% =
**26.8%**. See [IRS tax inflation adjustments for tax year 2026](https://www.irs.gov/newsroom/irs-releases-tax-inflation-adjustments-for-tax-year-2026-including-amendments-from-the-one-big-beautiful-bill).

## Out of scope (not modelled)

3.8% net investment income tax (§ 1411) · state and local tax · § 475(f) trader
mark-to-market election · § 461(l) excess business loss limitation ($313,000 /
$626,000 for 2025; sunset removed by P.L. 119-21 § 70601 for years beginning
after 2026) · straddle rules (§ 1092) · trader-versus-investor status ·
§ 988(e) personal transactions.
