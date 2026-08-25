# Workflows — exchange-for-physical-efp-transactions

The engine runs its checks in a fixed order and stops at the first failure. The order is
deliberate: each stage is only meaningful once the previous one has passed.

## 0. Establish the perspective

`futures_leg.side` and `physical_leg.side` describe **one party's** two legs — the party
running the engine. The counterparty holds the mirror image. Getting this backwards
inverts every subsequent conclusion, so fix it before anything else.

## 1. Structural validation (raises)

Malformed input is a programming error, not a compliance outcome, so it raises rather than
returning a rejection report:

- `contract_count` a positive `int`; `contract_multiplier` and `physical_quantity` positive
  and finite.
- All prices, rates and `time_to_expiry_years` finite; maturity non-negative.
- `side` values drawn from `VALID_FUTURES_SIDES` / `VALID_PHYSICAL_SIDES`.
- `efp_id` non-empty; if an attestation is supplied, `attested_by` non-empty.

Prices are **not** required to be positive. Physically settled commodity futures can trade
negative — CME WTI settled at −$37.63 on 2020-04-20 — so a non-positive spot is logged as
a warning (the carry relation is not meaningful there) rather than rejected.

## 2. Leg structure audit → `SIDE_DIRECTION_VIOLATION`

Valid pairings for one party are exactly:

| Futures leg | Physical leg |
|---|---|
| `BUY_FUTURES` | `SELL_PHYSICAL` |
| `SELL_FUTURES` | `BUY_PHYSICAL` |

Anything else is not an EFRP at all — it is two same-direction positions, doubling exposure
instead of exchanging it (ICE Futures U.S. Rule 4.06(b)(i)). Do not attempt to salvage the
basis; there is nothing to price.

## 3. Unit agreement → `UNIT_MISMATCH_REJECTION`

If both legs carry a `quantity_unit`, they must match (case-insensitively). Checked
**before** the quantity comparison, because bare numbers can agree while the trade does
not: 1,000 bbl is 42,000 gal. When either unit is omitted the check is skipped and a
warning is logged — a silent skip is how unit errors reach production.

Prices must be on the same per-unit basis as the quantities (USD per troy ounce, not per
contract). The engine cannot detect a per-contract price passed as a per-unit price; that
one is on the caller.

## 4. Quantity equivalence → `QUANTITY_MISMATCH_REJECTION`

    required_qty = contract_count * contract_multiplier
    allowance    = max(quantity_tolerance, quantity_tolerance_ratio * required_qty)
    reject if |physical_quantity - required_qty| > allowance

The report records `required_physical_quantity` and `quantity_deviation_ratio` on every
path, so a near-miss can be reviewed rather than merely refused.

Choose the tolerance from the venue and product, not from the default. The rules require
"approximately equivalent", not identical, and permit hedge ratios; Eurex publishes an
explicit ±20% band for the FX leg of an FX-futures EFP, which does not transfer to CME or
ICE. See `references/standards.md`.

When the deliverable quantity legitimately changes *after* execution, the venue's expected
remedy is a follow-up "true-up" EFP reported by the end of the business day on which the
actual delivery quantity is determined — not a silently mismatched original.

## 5. Bona fide attestation → `BONA_FIDE_ATTESTATION_MISSING` / `RULE_538_VIOLATION`

The engine cannot verify any of these; it records them and fails closed.

| Attestation | Requirement |
|---|---|
| `ownership_transfer_confirmed` | Bona fide transfer of ownership of the cash commodity, or a bona fide legally binding contract consistent with market convention (ICE 4.06(b)(ii); CME 538) |
| `non_transitory_confirmed` | Not contingent on another EFRP between the parties that offsets the related position without material market risk (ICE 4.06(b)(iii); CME 538.K) |
| `accounts_independently_controlled` | Independently controlled accounts, per one of the three permitted configurations (ICE 4.06(b)(iv); CME 538.B) |

`None` → `BONA_FIDE_ATTESTATION_MISSING`. Any flag False → `RULE_538_VIOLATION`, with
`attestation_failures` naming each unmet requirement and its rule. Neither produces a
payload. Where a narrow venue exception is being relied on (immediately offsetting FX EFPs,
IBA London Gold/Silver Auction EFPs at ICE), cite it in `supporting_document_ref` rather
than treating the general prohibition as inapplicable.

## 6. Basis evaluation

    observed_basis    = futures_price - spot_price
    net_carry         = r + u - y
    theoretical_basis = spot_price * (exp(net_carry * T) - 1)
    mispricing        = observed_basis - theoretical_basis

All three are computed on rejection paths as well as on approval: a rejected EFP still has
a real basis, and a zero-filled field cannot be told apart from a genuine zero.

Interpretation: for a consumption commodity, `F = S·exp((r+u-y)T)` is an upper bound, not
an equality. A positive mispricing is the enforceable side. A negative one says the market
is assigning a higher convenience yield than you assumed — a view, not a riskless trade.

## 7. Submission (outside this engine)

`efrp_clearing_payload` is an internal pre-submission record with
`reporting_status = "PENDING_SUBMISSION"`. Actual submission goes through the venue's own
facility — CME Direct / CME ClearPort, or ICE Block — within that venue's deadline:

- CME: as soon as possible, no later than the end of the business day of execution.
- ICE energy: by the end of the trading session; other ICE products: within 30 minutes of
  the **end of the session**; executed outside normal trading hours: within 5 minutes of
  the next session open.

Update the reporting status only after the venue acknowledges. Retain the title/contract
documents, the negotiation record, and the time of execution — see the recordkeeping
section of `references/standards.md`.
