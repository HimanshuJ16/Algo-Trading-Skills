# Workflows for Moscow Exchange (MOEX) Integration

The deep procedure behind `SKILL.md`. Every claim of fact is sourced in
`references/standards.md`.

## 0. Establish the sanctions position before writing any integration code

MOEX, National Clearing Center and NSD are on the OFAC SDN list under E.O. 14024
with an explicit secondary-sanctions warning, and the wind-down licences issued
at designation have expired. Because NCC is the central counterparty, the
clearing leg of an exchange trade is inside the block, not adjacent to it.

Determine, with counsel and before any technical work:

1. Which regimes bind your entity, its owners and its counterparties.
2. Whether any licence, authorisation or exemption covers the intended activity.
3. What screening evidence you must retain, and how often you re-screen.

Then encode the *outcome* as a fail-closed gate:

```python
screening = SanctionsScreening(
    cleared=True,
    regimes=("OFAC-SDN", "EU", "UK-OFSI"),
    screened_on=date(2026, 8, 26),
    reference="COMP-2026-0826",
)
config = MOEXSessionConfig(
    account="ACC_MOEX_01",
    client_code="CLIENT_99",
    sanctions_screening=screening,
    max_screening_age_days=30,
)
```

The module verifies that a decision was recorded and that it is not stale. It
does not verify the decision. Leave `max_screening_age_days` at `None` unless
your own policy sets a cadence — no public rule fixes one, and this skill will
not invent a number for you. When you do set it, pass `as_of` on every call so
the result is reproducible from the inputs.

## 1. Resolve the board, and confirm the interface serves it

| Board | Engine / Market | Trading system | Served by ASTS MFIX? |
|---|---|---|---|
| `TQBR` | `stock` / `shares` | ASTS | yes |
| `CETS` | `currency` / `selt` | ASTS | yes |
| `RFUD` | `futures` / `forts` | SPECTRA | **no** |

The MOEX public FIX 4.4 specification covers the FX and Securities markets only.
A FORTS order needs TWIME SPECTRA or Plaza II and a different message layout,
so the engine returns `MOEX_BOARD_NOT_ON_ASTS_MFIX` and builds nothing. The
price-limit helper `MOEXInstrument.is_within_exchange_limits()` still works on
RFUD instruments, so you can reuse the limit check while building the SPECTRA
message elsewhere.

Resolve unfamiliar boards against `https://iss.moex.com/iss/index.json` rather
than guessing from the code's shape.

## 2. Load reference data for the exact Symbol + Board pair

```python
instrument = MOEXInstrument(
    secid="VTBR", board="TQBR",
    lot_size=10000,          # ISS LOTSIZE
    min_step="0.005",        # ISS MINSTEP  -- pass as a string
    decimals=3,              # ISS DECIMALS
    currency="SUR",          # ISS CURRENCYID -- 'SUR', not 'RUB'
    source="ISS /engines/stock/markets/shares/boards/TQBR",
    as_of=date(2026, 8, 26),
)
```

Nothing here has a default, deliberately. Lot size on TQBR spans 1 to 1,000,000
and price steps span 0.005 to 0.5; any default would be wrong far more often
than right.

`MOEXInstrument` rejects internally inconsistent rows at construction — a step
with more decimal places than `decimals`, a non-positive step, a lot size below
1, an inverted limit band. That catches a mis-parsed ISS row before it reaches
an order.

Carry `source` and `as_of`. Reference data drifts: SBER's lot size is 1 today
and was not always, and price steps change with price bands.

**A listed instrument is not necessarily tradable.** On CETS, `USD000000TOD`
carries `STATUS='A'` and a current `PREVDATE` while showing zero trades, and
`EUR_RUB__TOM` likewise, following the June 2024 suspension of USD- and
EUR-settled instruments. `CNYRUB_TOM` had 94,102 trades on the same date. Gate
on activity — `NUMTRADES`, `TRADINGSTATUS`, a live top of book — not on listing.

## 3. Express the quantity in lots

MOEX Tag 38 is in lots: "Lot size is different for different Symbol + Board
combinations and should be determined from the marketdata feeds."

```python
# Either state lots directly...
MOEXOrderRequest(..., quantity_lots=100)

# ...or state units and let the instrument convert, which refuses a remainder.
MOEXOrderRequest(..., quantity_units=1_000_000)   # VTBR -> 100 lots
instrument.units_to_lots(15_000)                  # ValueError: 10000 or 20000
```

Supplying both, or neither, is a `ValueError` — an order whose size is ambiguous
should not be silently disambiguated. The report carries both `quantity_lots`
(what goes on the wire) and `quantity_units` (what you actually own afterwards),
so a mis-scaled order is visible in the audit trail rather than only in the fill.

## 4. Validate the price: step, sign, band, width

**Step.** MOEX rejects orders "with price that does not fit in minimal price
steps levels". The comparison must be exact, so prices and steps go through
`Decimal`, and floats are routed via `str` on conversion — a float `0.005` is
`0.005000000000000000104…`, which would turn a valid VTBR price into a spurious
off-step rejection.

**Sign.** Check positivity separately. `Decimal("-280.50") % Decimal("0.01")` is
zero, so a negative price passes the step test on its own.

**Alignment is opt-in and never aggressive.** The engine rejects an off-step
price rather than moving a caller's limit behind their back. When you do want it
moved:

```python
instrument.align_price_to_step("280.505", "BUY")   # -> 280.50 (down)
instrument.align_price_to_step("280.505", "SELL")  # -> 280.51 (up)
```

A BUY rounds down and a SELL rounds up, so alignment can only make the order
less aggressive. Rounding to nearest would push a buy *up* into the market.

**Band.** Prefer the Exchange-published `LOWLIMIT`/`HIGHLIMIT` where the board
publishes them; they are absolute per-instrument numbers, not a percentage. On
RFUD on 2026-08-26 the implied bands ran from ±5.03% (`92Q6`) to ±11.19%
(`A2U6`) — one constant is wrong for all but one instrument.

Where no band is published, declare your own and know what it is:

```python
MOEXOrderRequest(..., reference_price="280.00", max_price_deviation="0.05")
```

This is your risk policy, and the report labels it `CLIENT_POLICY` to keep it
distinguishable from `EXCHANGE_LIMITS` in an audit trail. A limit order with
neither control returns `MOEX_NO_PRICE_CONTROL` and is not built: a
reference price of zero or a missing band must not silently disable the check,
which is the failure mode this replaces.

**Width.** Tag 44 is capped at 10 characters including the decimal point, and
the price is rendered at the instrument's `DECIMALS` — 1 for LKOH, 2 for SBER,
3 for VTBR, 5 for CNYRUB_TOM. A high-priced instrument quoted to five decimals
overflows the field.

## 5. Build the MFIX body

```
11  ClOrdID            caller-supplied, <= 20 chars, not starting with '#'
453 NoPartyIDs = 1  \
448 PartyID            > client code, only for broker client accounts
447 PartyIDSource = D  |
452 PartyRole = 3     /
1   Account            <= 12 chars
386 NoTradingSessions = 1  \  must be adjacent, in this order,
336 TradingSessionID  = board  /  with nothing between them
55  Symbol             SECID, <= 12 chars
54  Side               1 buy / 2 sell
60  TransactTime       optional here; the session layer normally stamps it
38  OrderQty           lots
40  OrdType            2 limit / 1 market
44  Price              at DECIMALS; zero for a market order
59  TimeInForce        optional: 0 day / 3 IOC / 4 FOK
```

Points that are easy to get wrong:

- **There is no `BoardID` field.** The board is Tag 336, and the gateway
  "checks that tags 386 and 55 combination points to existing security" —
  a mismatch is rejected as 'Unknown Security'.
- **Do not send `SecurityExchange=MISX`.** Tag 207 and `MISX` appear nowhere in
  the MOEX FIX specification. `MISX` is the ISO 10383 operating MIC, for
  reference data and reporting; `RTSX` is its derivatives segment.
- **A market order carries Tag 44 = zero**, not an absent Tag 44. Passing a
  price with `ord_type="MARKET"` is a caller error and raises.
- **`fix_fields` is an ordered list; `fix_field_map` is not.** The map is for
  assertions and logging. Building a wire message from it loses the 386/336
  adjacency the specification requires.
- **No header or trailer is fabricated.** BeginString, BodyLength, MsgType,
  SenderCompID, TargetCompID, MsgSeqNum and CheckSum belong to your FIX engine.

## 6. Own the ClOrdID for the order's lifetime

`ClOrdID` is the idempotency key, so the module never generates one. A key
derived from the order's own fields — `MOEX_TQBR_SBER_100` — is identical for
every repeat of the same order, which is precisely when you need to tell them
apart, and its longer forms overflow String(20):
`MOEX_CETS_CNYRUB_TOM_1000` is 25 characters.

It must not begin with `#`: MOEX rejects Order Cancel (35=F) and Order
Cancel/Replace (35=G) requests whose ClOrdID does, so such an order cannot be
cancelled by client order ID afterwards. A `#` elsewhere in the string is fine.

Note also that MOEX processes New Order Single, Cancel and Cancel/Replace sent
over one SenderCompID synchronously — "new request is not sent to matching
engine until FIX server code receives reply to previous request" — and
recommends multiple sessions rather than sub-300-microsecond gaps on one.

## 7. Handle the ambiguous timeout correctly

If the request times out, MOEX may already have the order. Do not resubmit under
a fresh `ClOrdID`: resolve the order's state through the venue — an Order Status
Request (35=H), the Drop Copy stream, or Trade Capture — and reuse the original
identifier. See `order-placement-idempotency`.

## 8. Audit trail

Each `MOEXOrderReport` carries the status, the lot and unit quantities, the
price as a `Decimal`, which price control applied, and the ordered FIX fields.
Together with the `SanctionsScreening` reference and the instrument's `source` /
`as_of`, that is enough to reconstruct after the fact why an order was built,
what reference data it was built against, and on whose screening authority.
