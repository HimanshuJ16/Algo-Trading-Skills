---
name: moscow-exchange-moex-api-integration
description: >-
  Client-side pre-dispatch validation for Moscow Exchange (MOEX) orders — the fail-closed
  sanctions gate required because MOEX and its CCP are OFAC-designated, quantity in lots,
  minimum price step alignment, Exchange-published price limits, and MFIX (FIX 4.4)
  NewOrderSingle field construction with the board carried in the Tag 386/336 group.
domain: Exchange Integrations Global
subdomain: Eastern European Exchanges & MOEX Integration
tags: ["moex", "moscow-exchange", "iss-api", "mfix", "twime", "tqbr", "cets", "sanctions-screening"]
brokers_frameworks: ["MOEX ISS REST API", "MOEX MFIX Transactional (FIX 4.4)", "MOEX TWIME", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when building or auditing an order path into **Moscow Exchange**
(ISO 10383 operating MIC `MISX`) on the ASTS boards — `TQBR` equities and `CETS`
FX. It covers the checks that belong on the client side, before a message leaves
your process:

- **May this order path legally exist at all?** MOEX, National Clearing Center
  and NSD are on the OFAC SDN list. This is the first question, not the last.
- Is the quantity expressed in **lots**, and does the lot size match this exact
  Symbol + Board pair?
- Is the price on the instrument's **minimum price step**, at the instrument's
  **decimals**, and inside FIX Tag 44's 10-character cap?
- Is the price inside a price control — the Exchange-published band where the
  board publishes one, or a client-side policy band you declared?
- Are the MFIX fields the ones the MOEX FIX specification actually defines?

### The sanctions position, stated plainly

Verified against the OFAC SDN list downloaded 2026-08-26:

| Entity | SDN uid | Programs |
|---|---|---|
| MOSCOW EXCHANGE | 46526 | `UKRAINE-EO13662`, `RUSSIA-EO14024` |
| NATIONAL CLEARING CENTER (MOEX's CCP) | 46512 | `UKRAINE-EO13662`, `RUSSIA-EO14024` |
| NSD (central securities depository) | 37638 | `UKRAINE-EO13662`, `RUSSIA-EO14024` |

All three entries carry an explicit secondary-sanctions risk note referencing
Section 11 of E.O. 14024. The wind-down and divestment authorisations issued at
designation (General Licenses 99 and 100, as amended) have expired.

This skill does not tell you whether your trading is permitted — that depends on
your jurisdiction, your entity, your counterparties and licences you may hold,
and it is a question for counsel, not a Python module. What it does is refuse to
build an order until a screening result has been attached, so "nobody checked"
cannot be the reason an order goes out.

## When NOT to Use

- **Not sanctions advice and not a screening engine.** `SanctionsScreening`
  records that *you* screened and what you screened against. It looks nothing up
  and clears nothing. A `cleared=True` you passed in yourself is not diligence.
- **Not a transport.** Nothing here opens a socket, logs on, or sends an order.
  `ready_to_send` means "passed the checks modelled here", never "MOEX has the
  order". Session logon, sequence numbers, throttles, Cancel-on-Disconnect and
  recovery are out of scope.
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
- Python 3.9+. Standard library only (`decimal`, `dataclasses`, `datetime`, `re`,
  `logging`).

## Workflow

1. **Clear the sanctions gate before anything else, and fail closed.** An absent
   attestation is not clearance. Attach the regimes screened and the date; set
   `max_screening_age_days` if your policy requires re-screening, and pass
   `as_of` so the check is deterministic rather than clock-dependent.
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
  instrument bounds on the boards that have them, not a percentage. On RFUD on
  2026-08-26, `92Q6` was bounded at ±5.03% of its settlement price while `A2U6`
  was ±11.19% and `BTU6` ±11.08%. Hard-coding 5% rejects orders the exchange
  accepts and passes orders it rejects.
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

- A session with no `SanctionsScreening`, or one with `cleared=False`, ⟹
  `MOEX_SANCTIONS_GATE_NOT_CLEARED` and an empty `fix_fields` — the message is
  never built. A `cleared=True` screening with no regimes or no date ⟹
  `ValueError` at construction.
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
  `MOEX_NO_PRICE_CONTROL`. `reference_price=0` ⟹ `ValueError` — it no longer
  disables the check. Band boundary: 294.00 against 280.00 at 5% passes, 294.01
  does not.
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
- Run `python scripts/test_moscow_exchange_moex_api_integration.py` and confirm a
  100% pass rate.
- Against a MOEX test environment only, and only once your sanctions position
  permits it: submit one validated order and confirm the gateway accepts the
  (336, 55) pair. A symbology error unit tests cannot see is one where the board
  and symbol do not resolve to a security.

## Related Skills

- `order-placement-idempotency`
- `sanctions-screening-for-counterparties-and-instruments`
- `fix-protocol-session-management-across-venues`
- `exchange-tick-size-regime-tracking`
- `minimum-fill-size-and-lot-rounding-logic`
- `reference-data-symbol-mapping-across-vendors`
- `regional-broker-data-residency-constraints`
