---
name: exchange-for-physical-efp-transactions
description: >-
  Pre-submission validator and basis calculator for the Exchange for Physical (EFP) leg of an Exchange for Related Position (EFRP) — enforces the opposite-side leg structure, checks physical-to-futures quantity equivalence against a configurable venue tolerance, records the bona fide attestations required by CME Rule 538 and ICE Futures U.S. Rule 4.06, and prices the observed basis against cost of carry.
domain: Venue Integration & Derivatives
subdomain: Off-Exchange & Privately Negotiated Derivatives (EFRP)
tags: ["efp", "efrp", "cme-rule-538", "basis-trading", "futures-spot-swap", "physical-settlement", "commodities"]
brokers_frameworks: ["CME Rule 538", "ICE Futures U.S. Rule 4.06", "Eurex off-book EFP (Conditions for Trading, Number 4.3)", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill on physical commodity desks, spot-futures basis books, index-arbitrage operations, and institutional block/EFRP workflows, at the point where an EFP has been negotiated but **not yet submitted** to the venue. An **Exchange for Physical (EFP)** — one of the EFRP transaction types governed by **CME Rule 538** and **ICE Futures U.S. Rule 4.06** — is a privately negotiated, simultaneous exchange of an exchange-traded futures position for a corresponding related cash position: physical commodity, spot FX, or a physical equity basket. The two legs are struck together, which removes leg-execution risk and lets the parties trade the basis directly.

This skill covers the three things that get an EFP rejected or investigated after the fact: a leg structure that is not actually an exchange, a quantity that does not correspond to the futures leg, and a bona fide determination that nobody recorded.

## When NOT to Use

- **As a submission gateway.** The engine talks to no venue. EFRPs are submitted through CME Direct / CME ClearPort or ICE Block; `efrp_clearing_payload` is an internal pre-submission record, not a venue wire format, and its `reporting_status` stays `PENDING_SUBMISSION` until the venue acknowledges.
- **As proof that a trade is bona fide.** Whether title passed, whether the trade was transitory, and whether the accounts are independently controlled are facts about the outside world. The engine makes you attest to them and records who attested; it verifies nothing.
- **For hedge-ratio equivalence.** Rule 538 and Rule 4.06 permit appropriate hedge ratios to establish equivalence — an EFP-I against an index basket, or an EFR against a swap. This engine compares one physical quantity against `contracts x multiplier` in a single unit and is the wrong tool for those structures.
- **As a commodity pricing model.** The carry term here is a one-line sanity check on the negotiated basis. For implied convenience yield, full-carry bounds, per-unit storage costs and contango/backwardation regime detection, use `commodity-futures-storage-and-carry-cost-modeling`.
- **For post-expiry delivery mechanics.** Once the EFP is done, first-notice-date and delivery obligations belong to `physical-vs-cash-settlement-handling`.

## Prerequisites

- Futures leg: `futures_symbol`, `contract_count`, `contract_multiplier`, `futures_price_usd`, `side`.
- Physical leg: `physical_symbol`, `physical_quantity`, `spot_price_usd`, `side`.
- **Both legs quoted in the same unit**, with prices per that unit (USD per troy ounce, not per contract). Set `quantity_unit` on both so the engine can check it.
- The venue's quantity tolerance for this product, as a ratio — not an assumption carried over from another venue.
- Continuously compounded annual $r$, optional storage cost rate $u$ and convenience (or dividend) yield $y$, and time to expiry $T$ in years on a stated day-count basis.
- A named attester for the three bona fide requirements, plus a supporting document reference (warehouse receipt, bill of sale, FX confirmation).

## Workflow

1. **Leg Structure Audit** — the defining EFRP test:
   - Valid pairings for one party are exactly `BUY_FUTURES + SELL_PHYSICAL` or `SELL_FUTURES + BUY_PHYSICAL`. An EFRP requires the buyer of the related position to be the seller of the corresponding Exchange contract and vice versa (ICE Rule 4.06(b)(i)).
   - **Decision point — same-direction legs are not a rejected EFP, they are not an EFP at all.** They double the exposure instead of exchanging it. Reject as `SIDE_DIRECTION_VIOLATION` and do not re-price; there is nothing to fix in the basis.
2. **Unit and Quantity Equivalence Audit**:
   - $\text{Required Physical Quantity} = \text{Contract Count} \times \text{Contract Multiplier}$, in the futures leg's unit.
   - **Decision point — check the unit before the number.** Numbers can agree while the trade does not: 1,000 bbl is 42,000 gal. Mismatched units reject as `UNIT_MISMATCH_REJECTION` before the quantity comparison runs.
   - Allowance $= \max(\text{absolute tolerance},\ \text{tolerance ratio} \times \text{required qty})$. Exceeding it rejects as `QUANTITY_MISMATCH_REJECTION`, with the deviation ratio recorded.
   - **Decision point — pick the tolerance from the venue, not from the default.** The rules require the related position to be *approximately* equivalent (ICE Rule 4.06(b)(i); CME Rule 538.C), not identical. The 1e-4 default is a strict house control. Eurex, by contrast, permits the opposite FX transaction's nominal value to deviate from the FX future's by up to 20%.
3. **Bona Fide Attestation Record** (CME Rule 538 / ICE Rule 4.06(b)(ii)–(iv)):
   - Require an explicit `EfrpBonaFideAttestation` covering transfer of ownership, non-transitory execution, and account independence, naming the attester.
   - **Decision point — absence is not consent.** No attestation yields `BONA_FIDE_ATTESTATION_MISSING`; an attestation with a False flag yields `RULE_538_VIOLATION` naming the specific requirement and rule. Neither produces a payload.
4. **EFP Basis & Cost-of-Carry Fair Value**:
   - $\text{Observed Basis} = P_{\text{futures}} - P_{\text{spot}}$ (per unit).
   - $\text{Theoretical Basis} = P_{\text{spot}} \times \left(e^{(r + u - y)T} - 1\right)$, from $F = S e^{(r+u-y)T}$.
   - $\text{Mispricing} = \text{Observed} - \text{Theoretical}$.
   - **Decision point — the carry relation is an upper bound for a consumption commodity, not an equality.** A positive mispricing (basis richer than carry) is the enforceable side; a negative one is a view on convenience yield, not a riskless trade, because you generally cannot borrow and short the physical.
   - Basis figures are reported on rejection paths too — a rejected EFP still has a real basis, and a zero-filled field is indistinguishable from a genuine zero.
5. **Audit Report** — emit `EfpAuditReport`; submit through the venue's own EFRP facility, within that venue's deadline, and only then update the reporting status.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Trusting a leg-direction field nobody validates**: a `side` label that is recorded but never checked lets `BUY_FUTURES + BUY_PHYSICAL` pass as an "approved EFP". The result is double the intended long exposure booked under a compliance label that says the opposite.
- **Transitory EFRP violation**: executing offsetting spot/futures trades that collapse immediately without the incurrence of material market risk, contingent on another EFRP between the same parties (CME Rule 538.K; ICE Rule 4.06(b)(iii)). Narrow venue exceptions exist — immediately offsetting FX EFPs among them — but they are product-specific and must be cited, not assumed.
- **Stamping a submission that never happened**: writing `SUBMITTED` into a locally computed audit record makes the record false the moment the venue submission fails or is forgotten. Keep the status `PENDING_SUBMISSION` until the venue acknowledges.
- **Treating an exact-match tolerance as the regulatory standard**: the rules say "approximately equivalent" and permit hedge ratios; venues differ. An absolute tolerance is also meaningless across units, and a fixed 1e-4 that suits troy ounces is nonsense against a notional in millions.
- **Quantity discrepancy errors**: 10 gold futures (1,000 oz) matched against 900 oz of physical. When the deliverable quantity genuinely changes after execution, venues expect a follow-up "true-up" EFP, not a silently mismatched original.
- **Ignoring storage and convenience yield in the basis math**: pricing the fair basis off $e^{rT}$ alone omits $u$ and $y$ entirely and will call a fairly priced carry trade mispriced. For gold at \$2,490 over a quarter, a 2% storage rate moves the fair basis from \$25.02 to \$37.63 per ounce.
- **Assuming prices are positive**: physically settled commodity futures can trade negative (CME WTI settled at −\$37.63 on 2020-04-20). A validator that rejects negative prices blocks a real market state; one that prices carry off a negative spot reports a fair basis that means nothing. Flag it, do not silently price it.
- **Letting a NaN through**: a non-finite price propagates into an approved report whose basis, fair basis and mispricing are all NaN, and every downstream comparison against it is False. Reject non-finite inputs at the boundary.

## Verification

- Instantiate `ExchangeForPhysicalEngine()`. Evaluate 10 Gold futures (`GC_202612`, multiplier 100 troy oz, \$2,500.00/oz, `SELL_FUTURES`) against 1,000 oz of spot gold at \$2,490.00/oz (`BUY_PHYSICAL`), $T = 0.25$, $r = 4\%$, with a full `EfrpBonaFideAttestation`. Verify quantity equivalence ($1{,}000 = 10 \times 100$), observed basis $= +\$10.0000$, theoretical fair basis $= +\$25.0249$ (i.e. $2490 \times (e^{0.01} - 1)$), status `EFP_APPROVED`, and payload `reporting_status` $=$ `PENDING_SUBMISSION`.
- Add $u = 2\%$ storage: verify the fair basis widens to $+\$37.6315$. Set $u = 1\%$, $y = 9\%$: verify it inverts to $-\$24.7759$.
- Negative checks — each must be non-approved: `BUY_FUTURES` paired with `BUY_PHYSICAL` (`SIDE_DIRECTION_VIOLATION`); 900 oz against 1,000 (`QUANTITY_MISMATCH_REJECTION`); `BBL` against `GAL` (`UNIT_MISMATCH_REJECTION`); no attestation (`BONA_FIDE_ATTESTATION_MISSING`); `non_transitory_confirmed=False` (`RULE_538_VIOLATION`).
- Each must raise: NaN or infinite price/rate/maturity, negative maturity, zero or negative contract count, zero multiplier, negative physical quantity, a non-integer contract count, a blank `efp_id`, an unrecognised `side`, an anonymous attestation.
- Run `python -m unittest discover -s skills/exchange-for-physical-efp-transactions/scripts` and confirm all tests pass.

## Related Skills

- `commodity-futures-storage-and-carry-cost-modeling`
- `physical-vs-cash-settlement-handling`
- `dividend-futures-and-forward-modeling`
- `synthetic-continuous-futures-contract-construction`
