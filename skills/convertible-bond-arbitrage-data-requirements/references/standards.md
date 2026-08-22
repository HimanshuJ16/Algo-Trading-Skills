# Standards and Definitions for Convertible Bond Arbitrage Data

## 1. Definitional standards (market convention)

| Term | Definition | Source |
|---|---|---|
| Conversion value / parity | `conversion ratio x underlying share price` | CFA Level II curriculum notes, *Components of a Convertible Bond's Value* — [analystprep.com](https://analystprep.com/study-notes/cfa-level-2/calculate-and-interpret-the-components-of-a-convertible-bonds-value/) |
| Market conversion price | `convertible bond price / conversion ratio` | same |
| Market conversion premium ratio | `(market conversion price - share price) / share price`, algebraically identical to `(CB price - parity) / parity` | same |
| Straight value / investment value / bond floor | value of the option-free bond: PV of remaining coupons and principal discounted at a comparable-credit yield | same |
| Minimum value of a convertible | `max(conversion value, straight value)` | same |

These are definitions, not obligations: no regulator prescribes a conversion-premium
formula. What *is* required operationally is that a reported premium states its price
basis (clean or full), because the two differ by up to one coupon period of accrual.

## 2. Delta conventions — the engineering standard that matters

CB delta is quoted two ways, and mixing them mis-sizes the hedge by a factor of the
conversion ratio:

| Form | Range | Relation |
|---|---|---|
| Per-share delta (`Δ%`) | `[0, 1]` | `Δ% = Δ_shares / Cr` |
| Shares-per-bond delta | `[0, Cr]` | `Δ_shares = Δ% * Cr` |

Shares to short `= bonds x conversion ratio x per-share delta`. Any system boundary that
accepts a delta MUST validate its range against the convention it expects; the range
check is the only cheap defence against the wrong convention arriving from a vendor feed.

Confidence: high for the arithmetic relation (it is definitional). The prevalence of each
convention is desk- and vendor-specific — confirm against your own analytics feed's
documentation rather than assuming.

## 3. Carry components of a delta-hedged package

Cash inflows: coupon income; interest earned on short-sale proceeds held as collateral
(the "rebate"). Cash outflows: financing cost of the long convertible; stock borrow
(loan) fee; substitute payments for dividends owed on the short stock; prime-broker
haircuts on the proceeds balance. Convertible arbitrage is consequently a
funding-sensitive strategy — see AIMA Canada, *Convertible Arbitrage Strategy* (2006),
[aima.org](https://www.aima.org/static/4688e92f-c69e-4a57-a06a237795e45ea5/AIMA-Canada-Strategy-Paper-Convertible-Arbitrage-Strategy-2006.pdf).

Engineering standard: the borrow fee, the dividend obligation and the proceeds interest
are rates on the **short position market value** (`delta x parity`). Applying them to
bond notional is dimensionally wrong and misstates net carry.

Substitute payments: a short seller is contractually obliged to pay the lender an amount
equal to any dividend the lender would have received ("payment in lieu of dividend").
This is a securities-lending contractual obligation, not a tax or regulatory rule; US tax
information reporting for such payments is addressed separately at
[26 CFR § 1.6045-2](https://www.law.cornell.edu/cfr/text/26/1.6045-2).

## 4. Regulatory touchpoints of the short leg (US)

The short equity hedge is an ordinary short sale and carries the obligations of SEC
Regulation SHO (17 CFR §§ 242.200–204): order marking, the locate requirement before
effecting a short sale, the short sale price test circuit breaker, and close-out of
fails to deliver. This module does not enforce any of them — route the short leg through
`us-reg-sho-short-sale-locate-requirements`. Jurisdiction: United States only; EU/UK
short-sale disclosure obligations are separate (see
`eu-short-selling-regulation-disclosure-thresholds`).

## 5. Data-freshness standards

| Input | Requirement | Rationale |
|---|---|---|
| Stock spot | Recompute parity on every underlying tick used for a hedge decision | Parity is linear in spot; a stale spot mis-sizes the hedge directly |
| Borrow fee and availability | Refresh before sizing or resizing a short leg | Fees reprice intraday on HTB names; recall forces an unplanned unwind |
| Credit spread | Recompute the bond floor whenever the spread updates | The floor is the downside protection being paid for and is not constant |
| Delta | Rebalance on a calibrated drift band | Long gamma: delta drifts with spot. Band width is a cost/risk trade-off calibrated per name — there is no authoritative universal threshold |

## 6. Explicitly not claimed

- No specific delta-drift rebalance band, conversion-premium ceiling, vol-discount
  minimum, or carry floor is an industry or regulatory standard. The defaults in
  `ScreenThresholds` are illustrative and must be calibrated.
- This module does not price convertibles. Delta and implied volatility come from a
  credit-aware CB model (e.g. Tsiveriotis-Fernandes binomial) or a vendor feed.
- Issuer calls, soft-call triggers, investor puts, takeover/ratchet protection, dividend
  protection and contingent conversion are not modelled and materially affect real CB
  economics.
