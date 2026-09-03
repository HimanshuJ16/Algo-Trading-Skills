---
name: currency-gain-loss-tax-treatment-for-forex-trading
description: >-
  Use when estimating US federal tax on a currency trading book and comparing the two
  characterisations available: IRC 988 ordinary treatment as the default, the
  988(a)(1)(B) capital election, and Section 1256 60/40 treatment.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: tax-accounting-reporting-global
  tags: forex-tax, section-988, section-1256, 60-40-rule, ordinary-income, currency-gains, opt-out-election
  brokers_frameworks: "IRS Form 6781; IRS Form 1040 Schedule D; Python Dataclasses"
  version: "1.1.0"
  author: algo-trading-skills-contributors
---

> **NOT TAX ADVICE.** This skill models **US federal** income tax only, as an estimator for planning and record-keeping. Engage a qualified tax professional before making or relying on any election. Statutory citations are to the Internal Revenue Code (26 U.S.C.) and Treasury Regulations (26 C.F.R.).

## When to Use

Use this skill to estimate the US federal tax consequences of a currency trading book for one tax year, and to compare the two characterizations a position can receive:

- **IRC §988** — the default. "[A]ny foreign currency gain or loss attributable to a section 988 transaction shall be computed separately and treated as ordinary income or loss" (§988(a)(1)(A)). Ordinary losses are not subject to the capital loss cap.
- **IRC §1256** — the 60/40 split, "40 percent" short-term and "60 percent" long-term (§1256(a)(3)), with mandatory year-end mark-to-market (§1256(a)(1)) and reporting on Form 6781.

Three points correct the most common retail-forex misconception, and drive how this engine behaves:

1. **The §988(a)(1)(B) election produces *capital* character, not 60/40.** The statute permits electing to treat currency gain or loss "attributable to a forward contract, a futures contract, or option described in subsection (c)(1)(B)(iii) ... as capital gain or loss." It says nothing about 60/40. The 60/40 rate follows only if the contract independently qualifies as a §1256 contract.
2. **Spot transactions have no such election.** It reaches only forward, futures, and option contracts under §988(c)(1)(B)(iii); Treas. Reg. §1.988-3(b)(1) mirrors this.
3. **Whether a retail forex contract is a §1256(g)(2) "foreign currency contract" is unsettled.** *Wright v. Commissioner*, 809 F.3d 877 (6th Cir. 2016) read the definition broadly; proposed regulations REG-130675-17 (87 FR 40224, July 6, 2022) would limit it to foreign currency *forward* contracts and expressly overrule *Wright*. **The engine never infers eligibility** — the caller must assert `sec1256_eligible` per contract, on professional advice.

## When NOT to Use

- **As the basis for an actual filing or election.** It is an estimator, not a tax return preparer, and it does not produce a Form 6781.
- **Outside US federal tax.** No state or local tax, and no non-US jurisdiction. For other regimes see `multi-jurisdiction-tax-residency-implications`.
- **To determine whether your instrument qualifies under §1256(g)(2).** That is a legal determination the engine requires as an *input*, never as an output.
- **For the trader-versus-investor question, or a §475(f) mark-to-market election.** Both change the analysis materially and are out of scope.
- **Where straddles are present.** The §988(a)(1)(B) election is unavailable for a position that "is not part of a straddle" fails (§988(a)(1)(B); Treas. Reg. §1.988-3(b)(2)), and §1092 straddle rules are not modelled.

## Prerequisites

- A trade log for **one tax year**, each record typed `SPOT_FOREX`, `CURRENCY_FUTURES`, or `FORWARDS`, with realized PnL in USD and an ISO `YYYY-MM-DD` `trade_date`.
- Year-end mark-to-market PnL for any position open on the last business day of the year, needed for the §1256 scenario (§1256(a)(1)).
- A per-contract §1256 eligibility determination from a tax professional, supplied as `sec1256_eligible`.
- Marginal rates **as decimal fractions**: `ordinary_income_rate=0.37`, `ltcg_rate=0.20`, `stcg_rate=0.37`. Passing `37.0` is rejected — it is not silently treated as 37%.
- Optionally, for correct loss modelling: `prior_sec1256_gains_usd` (net §1256 gains in the 3 preceding years) and `other_capital_gains_usd`.

## Workflow

1. **Scope to the tax year**: pass `tax_year`. Decision point — omitting it includes every record regardless of `trade_date` and mixes tax years silently; the report emits a caveat when you do.
2. **Validate and bucket by instrument**: unknown instrument types, duplicate `trade_id`s, non-finite PnL, and mark-to-market on a position not open at year end are all rejected rather than absorbed. PnL is reported per instrument type, because spot and futures are not characterized alike.
3. **Determine §1256 eligibility per contract** — the gate, not an inference:
   - `sec1256_eligible=True` only on a professional determination under §1256(g)(2). Regulated currency futures are §1256 contracts under §1256(b)(1)(A); a rolling retail spot position generally is not.
   - `None` (undetermined) is treated as **ineligible** and raises an eligibility warning.
   - If no position is eligible, the recommendation is `INSUFFICIENT_ELIGIBILITY_BASIS` — the engine refuses to present a 60/40 comparison with no legal basis.
4. **§988 ordinary scenario**: realized PnL × ordinary rate. §988 has no mark-to-market regime, so year-end unrealized PnL is excluded here.
5. **§1256 60/40 scenario**: eligible positions only, realized **plus** year-end mark-to-market, taxed at the blended rate `0.60 × LTCG + 0.40 × STCG`. Positions that are not eligible stay ordinary in this scenario too, so both scenarios cover the same book and the reported saving is the value of electing, not an artefact of comparing different position sets.
6. **Loss waterfall — a §1256 loss is deferred, not forfeited**. Decision point: do not treat the excess over $3,000 as lost.
   - **§1212(c)**: a net §1256 contracts loss "shall be a carryback to each of the 3 taxable years preceding the loss year," limited to net §1256 gain in those years, character preserved 60/40. Elected on **Form 6781, box D**, amount on line 6.
   - **§1211(b)**: the remainder is allowed "to the extent of the gains from such sales or exchanges, plus" the lower of **$3,000 ($1,500 married filing separately)** or the excess.
   - **§1212(b)**: anything left carries forward indefinitely.
7. **Compare and recommend**: `ELECT_SECTION_1256` when the 60/40 scenario costs less, `REMAIN_SECTION_988` when ordinary treatment is better (typically loss years, where the ordinary loss escapes the §1211(b) cap), `INSUFFICIENT_ELIGIBILITY_BASIS` when eligibility was never established. Read `eligibility_warnings` and `caveats` before acting on any of them.
8. **For currency futures in a loss year, consider the reverse election**: §988(c)(1)(D)(ii) lets a taxpayer elect *into* §988 ordinary treatment for regulated futures contracts and nonequity options that would otherwise be §1256 contracts. The engine does not model it; note it as an option.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Believing the §988 opt-out delivers 60/40 on spot forex.** It delivers *capital* character, and only for forward/futures/option contracts. Spot has no election at all (§988(a)(1)(B), (c)(1)(B)(iii); Treas. Reg. §1.988-3(b)(1)).
- **Treating §1256 eligibility as settled.** REG-130675-17 would confine §1256(g)(2) to forward contracts and overrule *Wright*. Its final status is not verified here — confirm before relying on either position.
- **Making the election at filing time, or once for the year.** It is made **per transaction**, by identifying it in books and records "on the date the transaction is entered into" (Treas. Reg. §1.988-3(b)(3)), with a verification statement attached to the return (§1.988-3(b)(4)). A year-opening blanket memo does not satisfy this.
- **Modelling a §1256 loss as capped at $3,000 forever.** That skips the §1212(c) 3-year carryback and the indefinite §1212(b) carryforward, and materially overstates the case for staying in §988.
- **Forgetting that capital losses hit capital gains first.** §1211(b) allows losses "to the extent of the gains from such sales or exchanges" before the $3,000 cap — a trader with other capital gains loses much less to the cap than the naive model suggests.
- **Ignoring year-end mark-to-market.** §1256 contracts open on the last business day are marked to market whether or not you closed them; omitting that understates the §1256 scenario.
- **Passing rates as percentages.** `37.0` is not 37% to this engine — it is rejected, because silently accepting it overstates tax by 100×.
- **Assuming an ordinary §988 loss is always fully deductible.** Deductibility can still be limited, e.g. by the §461(l) excess business loss limitation ($313,000 / $626,000 for 2025, made permanent for years beginning after 2026 by P.L. 119-21 §70601). Not modelled.

## Verification

- Rates 37% / 20% / 37% → blended §1256 rate = 0.60 × 20% + 0.40 × 37% = **26.8%**.
- $100,000 gain on contracts asserted §1256-eligible: §988 tax **$37,000**, §1256 tax **$26,800**, savings **$10,200**, recommendation `ELECT_SECTION_1256`.
- Mixed book, $40,000 spot (ineligible) + $60,000 eligible futures: no-election tax **$37,000**; election tax = 40,000 × 37% + 60,000 × 26.8% = **$30,880**; savings **$6,120** = 60,000 × (37% − 26.8%), i.e. the rate delta on the eligible slice alone.
- $100,000 gain on `SPOT_FOREX` with eligibility undetermined: recommendation `INSUFFICIENT_ELIGIBILITY_BASIS`, §1256 scenario PnL $0, one eligibility warning.
- $50,000 §1256 loss with $30,000 of prior §1256 gains: carryback **$30,000**, $3,000 against ordinary, carryforward **$17,000**; §1256 benefit = 30,000 × 26.8% + 3,000 × 37% = **$9,150** (not $1,110); §988 benefit $18,500.
- Same loss with $10,000 of other capital gains: offset $10,000, $3,000 ordinary, carryforward $37,000, benefit **$3,790**.
- Married filing separately: cap $1,500, benefit **$555**.
- Open position with $30,000 year-end mark-to-market and $0 realized: §1256 scenario PnL $30,000 (tax $8,040), §988 scenario $0.
- `ordinary_income_rate=37.0`, a duplicate `trade_id`, an unknown instrument type, or mark-to-market on a closed position must each raise.
- Run `python -m unittest discover -s skills/currency-gain-loss-tax-treatment-for-forex-trading/scripts`.

## Related Skills

- `section-1256-contract-tax-treatment-us-futures`
- `capital-gains-vs-business-income-classification`
- `multi-currency-pnl-and-fx-conversion`
- `record-keeping-requirements-for-tax-audit-defense`
