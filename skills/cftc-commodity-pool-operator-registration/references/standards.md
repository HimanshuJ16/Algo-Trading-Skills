# Standards for CFTC 4.13(a)(3) De Minimis Exemption Monitoring

Jurisdiction: United States. Primary source: 17 CFR 4.13, Commodity Futures
Trading Commission. Quoted text below is from the codified rule; the paragraph
citations are given so each claim can be checked at source.

## The full set of conditions (all must hold, not just the trading tests)

| Citation | Condition | Enforced by this skill? |
|---|---|---|
| 4.13(a)(3)(i) | Pool interests are exempt from Securities Act registration and are marketed/advertised to the US public solely, if at all, in compliance with 17 CFR 230.506(c) or Rule 144A. | No — offering-document control. |
| 4.13(a)(3)(ii) | "At all times, the pool meets one or the other of the following tests with respect to its commodity interest positions" — the 5% margin test (A) **or** the 100% net notional test (B). | **Yes.** |
| 4.13(a)(3)(iii) | The operator reasonably believes each participant is an accredited investor, qualifying trust, knowledgeable employee, or qualified eligible person. | No — subscription/KYC control. |
| 4.13(a)(3)(iv) | "Participations in the pool are not marketed as or in a vehicle for trading in the commodity futures or commodity options markets". | No — marketing control. |
| 4.13(b)(1)–(2) | Notice of claim of exemption filed electronically with NFA, no later than delivery of subscription agreements to prospective participants. | No — filing obligation. |
| 4.13(b)(4) | The notice must be affirmed annually (or withdrawn) "within 60 days of the calendar year end through National Futures Association's electronic exemption filing system". | No — filing obligation. |

A pool that satisfies the arithmetic but misses the annual affirmation is not
exempt. Treat the engine's `allowed=True` as "the trading tests still hold",
never as "the exemption is intact".

## Test A — 5% margin test, 4.13(a)(3)(ii)(A)

> "The aggregate initial margin, premiums, and required minimum security
> deposit for retail forex transactions (as defined in § 5.1(m) of this
> chapter) required to establish such positions, determined at the time the
> most recent position was established, will not exceed 5 percent of the
> liquidation value of the pool's portfolio, after taking into account
> unrealized profits and unrealized losses on any such positions it has entered
> into; Provided, That in the case of an option that is in-the-money at the
> time of purchase, the in-the-money amount as defined in § 190.01 of this
> chapter may be excluded in computing such 5 percent"

Engineering consequences:

| Aspect | Standard |
|---|---|
| Measurement point | Determined at the time the most recent position was established — i.e. a pre-trade gate, evaluated per order. Mark-to-market drift between orders does not itself establish a position, but it does change the denominator the next order faces. |
| Denominator | Liquidation value **after** unrealized P&L. Static subscribed capital is not a permissible substitute. |
| Numerator | Initial margin **plus option premiums plus** the retail-forex minimum security deposit. Omitting premiums or forex deposits understates the ratio. |
| In-the-money options | For an option in-the-money at purchase, the in-the-money amount may be excluded. Note the cross-reference: 17 CFR 190.01 currently defines "in-the-money" (value of the underlying above the strike for a call, below for a put) rather than a separately captioned "in-the-money amount"; computing the excluded amount as the intrinsic value at purchase follows from that definition, but confirm the treatment with counsel before relying on the headroom it creates. |

## Test B — 100% net notional test, 4.13(a)(3)(ii)(B)

> "The aggregate net notional value of such positions, determined at the time
> the most recent position was established, does not exceed 100 percent of the
> liquidation value of the pool's portfolio, after taking into account
> unrealized profits and unrealized losses on any such positions it has entered
> into."

Notional is computed per instrument type, as prescribed by the rule:

| Instrument | Notional value |
|---|---|
| Futures | number of contracts × contract size in contract units (taking into account any multiplier specified in the contract) × current market price per unit. |
| Options | number of contracts × contract size, **adjusted by its delta**, in contract units (taking into account any multiplier) × **strike price** per unit. |
| Retail forex | the value in US Dollars of the transaction at the time it was established, **excluding** the US Dollar value of offsetting long and short transactions, if any. |
| Cleared swaps | the value determined consistent with the terms of 17 CFR part 45. |

Netting, per the same paragraph: the person "may net futures contracts with the
same underlying commodity across designated contract markets and foreign boards
of trade; and swaps cleared on the same derivatives clearing organization where
appropriate." The permission is keyed to the **underlying commodity** and the
venue/clearing dimension — not to matching maturities, and not to netting across
separate accounts as a general matter.

Engine default: **gross** aggregation. This is the conservative direction — it
can reject a trade the rule would have permitted, but never permits one the rule
forbids. A pool that nets upstream in line with the paragraph above should feed
already-netted figures into `PortfolioState`.

## Currency of this reference

- Rule text verified against 17 CFR 4.13 as codified (paragraph structure
  reflecting the 2020 amendments that introduced the 230.506(c)/144A language
  in (a)(3)(i) and the bad-actor certification in 4.13(b)).
- CFTC Staff Letter 25-50 (Division of Market Participants, 19 December 2025)
  grants no-action relief from CPO registration to SEC-registered investment
  advisers that file Form PF for the covered funds, subject to conditions
  including QEP-level investor eligibility and initial/annual notices to staff,
  while the Commission considers whether to reinstate the former 4.13(a)(4)
  exemption. It is a staff position, not a rule, and does not change the
  4.13(a)(3)(ii) thresholds — but a pool failing those thresholds may have this
  alternative path available. Confirm its current status before relying on it.
