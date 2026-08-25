# EFP Pre-Submission Checklist

Sign off before the EFP is submitted to the venue. Every "no" is a blocker, not a note.

## Leg structure

- [ ] Are the two legs on **opposite** sides — `BUY_FUTURES`/`SELL_PHYSICAL` or `SELL_FUTURES`/`BUY_PHYSICAL` (ICE Rule 4.06(b)(i); CME Rule 538)?
- [ ] Are the sides recorded from **this party's** perspective, not the counterparty's?
- [ ] Is the related position an eligible one — the underlying, a by-product, a related product or a correlated OTC instrument — and *not* a futures contract or an option on futures?

## Quantity and units

- [ ] Are both legs quoted in the **same unit**, with prices per that unit rather than per contract?
- [ ] Does the physical quantity equal $\text{contract count} \times \text{multiplier}$ within the tolerance **this venue and product** allow?
- [ ] Is that tolerance taken from the venue's own rules rather than carried over from another venue or left at the library default?
- [ ] If the quantity deviates, is the bona fide reason for the structure documented and explainable on request?

## Bona fide requirements (CME Rule 538 / ICE Rule 4.06(b)(ii)–(iv))

- [ ] Has a **named** person or desk attested to the transfer of ownership (or a binding contract consistent with market convention)?
- [ ] Has the same attester confirmed the EFP is **not transitory** — not contingent on another EFRP between the parties that offsets the position without material market risk?
- [ ] Are the accounts independently controlled under one of the three permitted configurations?
- [ ] If relying on a narrow venue exception (immediately offsetting FX EFPs, IBA London Gold/Silver Auction EFPs at ICE), is the exception cited by name?
- [ ] Is a supporting document reference attached — warehouse receipt, bill of sale, FX confirmation?

## Basis

- [ ] Is the fair basis computed with storage cost $u$ and convenience/dividend yield $y$, not financing alone?
- [ ] Are $r$, $u$, $y$ continuously compounded on the same day-count basis as $T$?
- [ ] For a consumption commodity, is a negative mispricing being read as a convenience-yield view rather than as an arbitrage?
- [ ] If spot or futures is non-positive, has the carry-based fair value been set aside as meaningless rather than traded off?

## Submission and records

- [ ] Is the payload still marked `PENDING_SUBMISSION` — i.e. nothing claims a submission that has not happened?
- [ ] Is the EFP going through the venue's own facility (CME Direct / CME ClearPort, ICE Block)?
- [ ] Is it inside that venue's deadline (CME: end of the business day of execution; ICE energy: end of session; other ICE products: 30 minutes after the end of session; outside trading hours: 5 minutes after the next open)?
- [ ] Is the date and time of execution captured, and are title/contract and negotiation records retained and producible on request?
- [ ] Is the reporting status updated only **after** the venue acknowledges?
