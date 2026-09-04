---
name: moscow-exchange-moex-api-integration
description: >-
  Use when a trading system must be structurally prevented from routing to a sanctioned
  venue while keeping a documented, reviewable adapter on file. Moscow Exchange and its
  OFAC-designated CCP are the worked example of the fail-closed gate.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: global-market-integration
  tags: sanctions-gate, ofac-sdn, fail-closed, moex, moscow-exchange, iss-api, mfix, twime, tqbr, cets, sanctions-screening
  brokers_frameworks: "MOEX ISS REST API; MOEX MFIX Transactional (FIX 4.4); MOEX TWIME; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when a trading system must be **structurally prevented from
routing orders to a sanctioned venue**, while still keeping a documented,
reviewable adapter for that venue on file. Moscow Exchange (ISO 10383 operating
MIC `MISX`) is the worked example: MOEX, its central counterparty the National
Clearing Center, and the National Settlement Depository are designated on the
OFAC SDN list, so the clearing leg of an exchange trade is inside the block, not
adjacent to it.

The deliverable is the pattern, not the venue:

- **A fail-closed gate in front of message construction.** With no dated
  screening attestation attached to the session, no order object is built at
  all — every path returns a refusal with empty `fix_fields`. Absence of an
  answer is not clearance, and "nobody checked" cannot be the reason an order
  goes out.
- **An attestation that is an audit artefact, not a lookup.** It records who
  screened, against which regimes, on what date, and under what reference, so a
  reviewer can reconstruct on whose authority a path was opened.
- **A staleness rule the operator sets deliberately**, evaluated against a
  caller-supplied date rather than a clock read inside the module, so a refusal
  is reproducible from its inputs months later.
- **Venue protocol detail kept behind the gate as inert reference material.**
  Quantity in lots, minimum price step alignment, Exchange-published price
  limits and the MFIX (FIX 4.4) `NewOrderSingle` field layout are documented and
  unit-tested so the adapter is reviewable — but nothing here opens a socket,
  logs on, or sends anything, and no path emits a message while the gate is
  closed.

The same shape applies to any venue whose legality is a precondition rather than
an assumption: encode the precondition as code that refuses, not as a comment
that asks.

### The sanctions position, stated plainly

MOEX, National Clearing Center and NSD were designated by OFAC on 12 June 2024
under E.O. 14024, and each designation carries an explicit secondary-sanctions
risk note. The wind-down and divestment authorisations issued at designation
(General Licenses 99 and 100, as amended) have expired. Other jurisdictions
maintain their own measures on Russian financial infrastructure, on their own
timelines.

Confirm current status yourself against the OFAC sanctions list search,
<https://sanctionssearch.ofac.treas.gov/>. Designations, licences and their
scope change; nothing in this repository is a screening result.

This skill does not tell you whether your trading is permitted — that depends on
your jurisdiction, your entity, your counterparties and licences you may hold,
and it is a question for counsel, not a Python module. What it does is refuse to
build an order until a screening result has been attached, so "nobody checked"
cannot be the reason an order goes out.

## When NOT to Use

- **Not a route to trading a sanctioned venue.** Nothing here establishes that
  an order path into MOEX is lawful for you, and a gate that opens is not a
  permission. If what you want is the steps to start sending live orders to
  MOEX, this skill is not it and does not contain them.
- **Not sanctions advice and not a screening engine.** `SanctionsScreening`
  records that *you* screened and what you screened against. It looks nothing up
  and clears nothing. A `cleared=True` you passed in yourself is not diligence.
  For screening counterparties and instruments against lists, see
  `sanctions-screening-for-counterparties-and-instruments`.
- **Not a transport.** Nothing here opens a socket, logs on, or sends an order.
  `ready_to_send` means "passed the checks modelled here", never "MOEX has the
  order". Session logon, sequence numbers, throttles, Cancel-on-Disconnect and
  recovery are out of scope; see
  `fix-protocol-session-management-across-venues`.
- **Not an ISS client.** Lot size, price step, decimals and price limits are
  **inputs**. This module does not fetch them and deliberately supplies no
  defaults for them — MOEX has no universal lot size and no universal tick.
- **Not for the Derivatives market.** The MOEX public FIX 4.4 specification is
  "valid for Moscow Exchange FX and Securities (Main and T+2) markets only".
  FORTS (`RFUD`) runs on the SPECTRA system and is reached over TWIME SPECTRA or
  Plaza II with a different message layout. The engine refuses `RFUD` rather
  than emitting an ASTS message for it; the price-limit helper still works there.
- **Not for negotiated deals, REPO with CCP, FX swaps or auction-only order
  types.** MOEX layers extra semantics on these (`SecurityType`, `PriceType`,
  `TradeThruTime`) that this module does not model.
- **Not a position-limit, margin or reporting tool.**

## Prerequisites

- A cleared, dated sanctions screening for the order path, with the regimes
  screened against recorded.
- Per-instrument reference data for the exact **Symbol + Board** pair, from the
  MOEX ISS securities block: `LOTSIZE`, `MINSTEP`, `DECIMALS`, `CURRENCYID`, and
  `LOWLIMIT`/`HIGHLIMIT` where the board publishes them.
- Session identity: FIX Tag 1 `Account` (≤ 12 chars) and, for broker client
  accounts, the client code (≤ 12 chars).
- A caller-generated `ClOrdID`, unique per order and ≤ 20 characters.
- The MOEX FIX specification for the interface you are certified against.
- Python 3.10+. Standard library only (`decimal`, `dataclasses`, `datetime`, `re`,
  `logging`).

## Workflow

1. **Clear the sanctions gate before anything else, and fail closed.** This is
   the whole point of the module: the gate runs before board resolution, before
   quantity conversion, before any field is formatted, and a missing, revoked or
   expired attestation stops the order there with nothing built. An absent
   attestation is not clearance. Attach the regimes screened and the date; set
   `max_screening_age_days` if your policy requires re-screening, and pass
   `as_of` so the check is deterministic rather than clock-dependent.

   Everything from step 2 onward is venue protocol detail that only ever runs
   behind an open gate.
2. **Resolve the board, and check the interface serves it.** `TQBR` (stock /
   shares) and `CETS` (currency / SELT) are ASTS and reachable over MFIX. `RFUD`
   (futures / FORTS) is SPECTRA and is not — building an ASTS `NewOrderSingle`
   for it produces a message no MOEX gateway accepts.
3. **Load reference data for the exact Symbol + Board pair, and refuse a
   mismatch.** Lot size and price step are per pair. A `TQBR` row used for a
   `CETS` order mis-sizes the order silently.
4. **Convert the quantity to lots — Tag 38 is in lots, never in shares.** MOEX
   states the lot size "is different for different Symbol + Board combinations
   and should be determined from the marketdata feeds". A quantity that is not a
   whole number of lots is not expressible; raise rather than round it.
5. **Check the price against the instrument's price step, and check positivity
   separately.** A negative price is an exact multiple of any step, so the
   modulo test alone passes it. If the price is off-step, **reject it — do not
   silently move the caller's limit.** When you do want it moved, call
   `align_price_to_step()`, which rounds a BUY down and a SELL up so alignment
   can never make the order more aggressive.
6. **Apply a price control, and refuse to send a limit order with none.** Where
   the board publishes `LOWLIMIT`/`HIGHLIMIT`, use those absolute bounds. Where
   it does not, declare your own `reference_price` + `max_price_deviation` band
   and label it what it is: your risk policy, not a MOEX rule.
7. **Render the price at the instrument's `DECIMALS`, then check the width.**
   MOEX caps Tag 44 at 10 characters including the decimal point, so a
   high-priced instrument quoted to five decimals overflows it.
8. **Build the MFIX body with the tags the specification defines.** The board is
   FIX Tag 336 `TradingSessionID` inside the Tag 386 `NoTradingSessions` group,
   which must contain exactly one element with 386 immediately followed by 336
   and nothing between them. The client code goes in `<Parties>` as PartyID
   (448) with PartyIDSource (447) `D` and PartyRole (452) `3`. The session layer
   owns the header and trailer; do not fabricate them.
9. **Carry your own `ClOrdID` through the order's whole lifecycle.** It is the
   idempotency key. Never derive it from the order's own fields — two identical
   orders would then collide — and never start it with `#`, which makes the
   order uncancellable by client order ID.

> Full procedure: see `references/workflows.md`.
> Rule citations and verified reference data: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Routing to MOEX without a sanctions position.** This is the headline error
  and it is not a paperwork one: MOEX's central counterparty is itself
  designated, so the clearing leg is inside the block, and the SDN entries carry
  an explicit secondary-sanctions warning. An integration that never asks the
  question has answered it by default.
- **Assuming a fixed ±5% price collar.** MOEX publishes **absolute** per-
  instrument bounds on the boards that have them, not a percentage. Contracts
  sitting on the same FORTS board on the same day carry implied bands that
  differ by a factor of two or more, so no single percentage reproduces the
  exchange's bounds: hard-coding 5% rejects orders the exchange accepts and
  passes orders it rejects. Consume `LOWLIMIT`/`HIGHLIMIT` as absolute numbers.
- **Putting shares in Tag 38.** `OrderQty` is in lots. On TQBR alone the lot
  size runs 1, 10, 100, 1,000, 10,000, 100,000 and 1,000,000 across the board.
  `VTBR` has a 10,000-share lot: sending "100" meaning 100 shares buys a million.
- **Defaulting the tick to 0.01.** `SBER` steps in 0.01, but `LKOH` and `MGNT`
  step in 0.5, `ROSN` in 0.05 and `VTBR` in 0.005. MOEX rejects a price that
  does not fit the minimal price step, so a plausible-looking `4126.01` on LKOH
  never reaches the book.
- **Silently rounding the limit price to the tick.** Rounding a buy *up* makes
  the order more aggressive than the caller asked for. If you round at all,
  round away from the market — and prefer rejecting, so the caller learns their
  reference data is wrong.
- **Sending `SecurityExchange=MISX`.** Tag 207 and the string `MISX` appear
  nowhere in the MOEX public FIX 4.4 interface specification. `MISX` is the ISO
  10383 MIC for reporting and reference data; the board goes in Tag 336.
- **Inventing a `BoardID` field.** There is no such FIX tag. The board is Tag
  336 inside the 386 group, and MOEX rejects the order with 'Unknown Security'
  if the (336, 55) pair does not resolve.
- **Deriving `ClOrdID` from the order's contents.** A key like
  `MOEX_TQBR_SBER_100` is identical for every repeat of the same order — which
  is exactly when you most need to tell them apart — and
  `MOEX_CETS_CNYRUB_TOM_1000` is 25 characters against a String(20) field.
- **Formatting every price to a fixed number of decimals.** `DECIMALS` is 1 for
  LKOH, 2 for SBER, 3 for VTBR and 5 for CNYRUB_TOM. A fixed `%.4f` is wrong
  almost everywhere and can breach the 10-character Tag 44 cap.
- **Reading `STATUS='A'` as "tradable".** On 2026-08-26 `USD000000TOD` was still
  listed on CETS with an active status and a current `PREVDATE`, and had zero
  trades that session, while `CNYRUB_TOM` had 94,102. MOEX suspended USD and EUR
  instruments on 13 June 2024 following the sanctions. Check activity, not just
  listing.
- **Treating "TWIME" as one protocol.** TWIME SPECTRA serves the Derivatives
  market; TWIME ASTS, launched 18 March 2024, serves the Equity & Bond and FX
  markets. They are different endpoints with different message sets, and neither
  is MFIX.
- **Labelling the currency RUB.** MOEX ISS reports `CURRENCYID` / `FACEUNIT` as
  `SUR` for rouble-denominated instruments. Map it deliberately.
- **Retrying an order because the request timed out.** MOEX may already have it.
  Resolve the state through the venue and reuse the original `ClOrdID`; a retry
  under a fresh ID is a second position. See `order-placement-idempotency`.

## Verification

- **The gate fails closed on every order path.** A missing attestation
  (`sanctions_screening=None`, which is what a default `MOEXSessionConfig`
  carries), a revoked one (`cleared=False`) and one expired against
  `max_screening_age_days` each ⟹ `MOEX_SANCTIONS_GATE_NOT_CLEARED`,
  `ready_to_send=False` and an empty `fix_fields` — for a LIMIT order, a MARKET
  order, a `TQBR` order, a `CETS` order and an `RFUD` order alike. The closed
  state is what you get by forgetting, not something you opt into.
- **The gate runs first.** An order that would also fail another check — an
  unknown board, an over-length `ClOrdID`, an off-step price — still reports the
  sanctions status, so a closed gate can never be masked by a later rejection.
- A `cleared=True` screening with no regimes or no date ⟹ `ValueError` at
  construction: an attestation that records nothing is not an attestation.
- With `max_screening_age_days=25` and a screening dated 2026-08-01: `as_of`
  2026-08-26 (exactly 25 days) passes, 2026-08-27 does not. With no max age
  configured, a 2020 screening passes — the module invents no cadence. With a
  max age configured but no `as_of` ⟹ `ValueError`, not a silent pass.
- `VTBR` (lot 10,000) with `quantity_units=1_000_000` ⟹ 100 lots in Tag 38;
  `units_to_lots(15_000)` ⟹ `ValueError` naming 10,000 and 20,000 as the nearest
  whole-lot quantities. `GAZP` (lot 10) at 100 lots ⟹ 1,000 shares.
- `SBER` at `280.505` ⟹ `MOEX_PRICE_STEP_BREACH` with `report.price` unchanged;
  `LKOH` at `4126.01` against its 0.5 step ⟹ the same. `align_price_to_step`
  gives 280.50 for a BUY and 280.51 for a SELL, 4126.0 and 4126.5 on LKOH.
- `-280.50` is an exact multiple of 0.01 (`is_on_step` returns True) and is still
  refused — positivity is checked separately. `NaN`, `Infinity`, `"not-a-price"`
  and `True` ⟹ `ValueError`/`TypeError`, not an approved order.
- `VTBR` at the float `51.165` against a 0.005 step ⟹ validated, price rendered
  `"51.165"`; binary float error does not turn a valid price into a rejection.
- A limit order with no published band and no declared policy band ⟹
  `MOEX_NO_PRICE_CONTROL`. `reference_price=0` ⟹ `ValueError` — a zero
  reference must not silently disable the check. Band boundary: 294.00 against
  280.00 at 5% passes, 294.01 does not.
- `92Q6` published bounds 70240/77680: `is_within_exchange_limits` is True at
  both bounds and False at 70239.99 and 77680.01, and returns `None` for an
  instrument with no published band. An instrument carrying only a `high_limit`
  is not treated as banded.
- A validated SBER order emits exactly
  `[(11,…),(453,"1"),(448,…),(447,"D"),(452,"3"),(1,…),(386,"1"),(336,"TQBR"),(55,"SBER"),(54,"1"),(60,…),(38,"100"),(40,"2"),(44,"280.50")]`
  — 386 immediately followed by 336, no Tag 207, no `MISX`, and no session
  header or trailer tags (8, 9, 35, 34, 49, 56, 10).
- A `MARKET` order ⟹ Tag 40 `1` and Tag 44 `0.00`; a MARKET order carrying a
  price ⟹ `ValueError`.
- `ClOrdID` `"MOEX_CETS_CNYRUB_TOM_1000"` (25 chars) ⟹
  `MOEX_FIELD_LENGTH_BREACH`; 20 chars passes; `"#ORD-1"` is refused while
  `"ORD#1"` passes.
- `RFUD` ⟹ `MOEX_BOARD_NOT_ON_ASTS_MFIX` with empty `fix_fields`.
- Run `python -m unittest discover -s skills/moscow-exchange-moex-api-integration/scripts`
  and confirm a 100% pass rate. `TestSanctionsGate` and
  `TestSanctionsGateFailsClosedOnEveryPath` are the cases that hold the gate
  shut; a change that lets an order be built without an attestation must fail
  them.
- Know the boundary of what these checks can prove: whether a (336, 55) pair
  resolves to a real security is only observable at the venue, and this skill
  deliberately never goes there. Treat the FIX field list as a reviewable
  artefact, not as evidence of an accepted order.

## Related Skills

- `order-placement-idempotency`
- `sanctions-screening-for-counterparties-and-instruments`
- `fix-protocol-session-management-across-venues`
- `exchange-tick-size-regime-tracking`
- `minimum-fill-size-and-lot-rounding-logic`
- `reference-data-symbol-mapping-across-vendors`
- `regional-broker-data-residency-constraints`
