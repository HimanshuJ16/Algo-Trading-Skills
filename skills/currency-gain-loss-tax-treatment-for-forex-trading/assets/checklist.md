# Pre-Flight Checklist

**US federal tax only. Not tax advice — confirm with a qualified tax professional.**

## Data
- [ ] Does the trade log cover exactly one tax year, and is `tax_year` passed explicitly?
- [ ] Is every record typed `SPOT_FOREX`, `CURRENCY_FUTURES`, or `FORWARDS`, with a unique `trade_id`?
- [ ] Is year-end mark-to-market PnL supplied for every position open on the last business day (IRC § 1256(a)(1))?
- [ ] Are marginal rates passed as decimal fractions (`0.37`, not `37.0`)?
- [ ] Are `prior_sec1256_gains_usd` and `other_capital_gains_usd` supplied, so the loss waterfall is not understated?
- [ ] Is filing status set (`married_filing_separately`) so the correct § 1211(b) cap applies?

## Legal determinations (cannot be inferred from the data)
- [ ] Has a tax professional determined, per contract, whether it is a § 1256 contract under § 1256(g)(2)?
- [ ] Has the unsettled status of REG-130675-17 versus *Wright v. Commissioner* been considered for any non-exchange-traded position?
- [ ] Is it understood that the § 988(a)(1)(B) election gives **capital character**, and 60/40 only if the contract is independently a § 1256 contract?
- [ ] Is it understood that **spot transactions have no § 988(a)(1)(B) election**?
- [ ] For a loss year on currency futures, has the reverse § 988(c)(1)(D)(ii) election into ordinary treatment been considered?

## Election record-keeping
- [ ] Is each elected transaction identified in books and records **on the date it is entered into** (Treas. Reg. § 1.988-3(b)(3)) — not once at the start of the year, not at filing?
- [ ] Is the identification method consistently applied across all elected transactions?
- [ ] Is the verification statement prepared for attachment to the return (Treas. Reg. § 1.988-3(b)(4))?
- [ ] Is no elected position part of a straddle (§ 988(a)(1)(B); Treas. Reg. § 1.988-3(b)(2))?

## Output review
- [ ] Were `eligibility_warnings` read, and is the recommendation something other than `INSUFFICIENT_ELIGIBILITY_BASIS`?
- [ ] Were `caveats` read, including the excluded items (NIIT, state tax, § 475(f), § 461(l), straddles)?
- [ ] Is the reported `sec1256_loss_carryforward_usd` tracked into future years rather than treated as lost?
- [ ] Is Form 6781 box D checked, with the carryback amount on line 6, if a carryback is being claimed?
