# Broker & Framework Coverage — broker-order-type-capability-matrix

## How to read this table

This is a **starting template, verified against the linked documentation — not a live
feed.** Order-type support changes, and it varies by asset class, account type,
entitlement and API surface within a single broker. "Native" below means *this
broker's documented API exposes the order type directly*; it does not promise the
type is available for every instrument, product or venue that broker reaches.

Two failure directions, and they are not symmetric. Marking a type **emulated when it
is actually native** costs latency and a little unnecessary local state. Marking it
**native when it is not** produces a rejected order, or worse a silently different
one. When the documentation is ambiguous, leave the order type out of
`native_order_types` and let the synthesizer handle it.

| Broker | Bracket | OCO | Iceberg | TWAP | VWAP | Pegged | MOC | Fractional |
|---|---|---|---|---|---|---|---|---|
| IBKR | Native | Native (OCA) | Native (`displaySize`) | Native (IBALGO) | Native (IBALGO) | Native | Native | Yes |
| Alpaca | Native (order class) | Native (order class) | Emulated | Emulated | Not supported | Not supported | Native (TIF `cls`) | Yes, restricted |
| Zerodha | Emulated | Emulated (GTT caveat) | Native (`variety=iceberg`) | Emulated | Not supported | Not supported | Not supported | No |
| Binance Spot | Native (OTOCO) | Native | Native (`icebergQty`) | Native (Algo API only) | Not supported | Not supported | Not supported | Yes (base-asset step) |

"Emulated" means this skill's synthesizer decomposes it. "Not supported" means neither
the broker nor this skill provides it — see *Order types this skill will not emulate*.

## 1. Interactive Brokers (TWS API)

- **Bracket, OCA**: `Order.ocaGroup` assigns orders to a One-Cancels-All group;
  `Order.ocaType` decides what happens to the survivors — `1` cancel all remaining
  *with block*, `2` proportionately reduce *with block*, `3` proportionately reduce
  with no block. The "with block" variants exist specifically for overfill
  protection: *"only one order in the group will be routed at a time to remove the
  possibility of an overfill."* An OCA group configured with `ocaType=3` therefore
  carries the same double-execution exposure as a locally emulated OCO.
- **Iceberg**: `Order.displaySize` — *"The publicly disclosed order size, used when
  placing Iceberg orders."* The `Dark Ice` IBALGO is a separate, more active variant.
- **TWAP / VWAP**: IBALGO strategies. Both are documented as targeting the average
  price *from submission to the market close*, and both are listed for **US
  equities**. Do not assume they are available for every product on every exchange
  IBKR reaches.
- **Pegged family, MOC/LOC, MOO/LOO, trailing stop**: all documented order types.

Sources:
[TWS API — Basic Orders](https://interactivebrokers.github.io/tws-api/basic_orders.html),
[TWS API — OCA Orders](https://interactivebrokers.github.io/tws-api/oca.html),
[TWS API — Order class](https://interactivebrokers.github.io/tws-api/classIBApi_1_1Order.html),
[TWS API — IB Algos](https://interactivebrokers.github.io/tws-api/ibalgos.html).

## 2. Alpaca Trading API

- **Order types**: `market`, `limit`, `stop`, `stop_limit`, `trailing_stop`.
- **Order classes**: `simple`, `bracket`, `oco`, `oto`. OCO is documented as *"a set
  of two orders with the same side"* — the constraint the synthesizer mirrors.
- **MOC / LOC**: via `time_in_force="cls"` (and `"opg"` for the opening auction),
  subject to Alpaca's submission cut-offs.
- **Not supported**: iceberg, pegged orders, and TWAP/VWAP execution algos.
- **Fractional restriction that bites**: fractional orders accept
  `time_in_force="day"` only — `gtc`, `ioc`, `fok`, `opg` and `cls` are rejected. A
  fractional quantity therefore cannot ride the advanced order classes or the closing
  auction. `supports_fractional=True` on the profile is **not** a statement that
  fractional works with bracket/OCO.

Source: [Alpaca — Orders at Alpaca](https://docs.alpaca.markets/docs/orders-at-alpaca).

## 3. Zerodha Kite Connect

- **Varieties**: `regular`, `amo`, `co`, `iceberg`, `auction`. **`bo` (bracket order)
  is absent from the current API** — bracket orders were withdrawn and must be
  emulated.
- **Order types**: `MARKET`, `LIMIT`, `SL`, `SL-M`.
- **Iceberg**: `variety="iceberg"` with `iceberg_legs` — *"number of legs per Iceberg
  should be between 2 and 50"* — and `iceberg_quantity` (quantity per leg). Feed this
  bound into `iceberg_slices`; the planner does not know it.
- **OCO — read the caveat.** Kite *does* offer a two-leg OCO: *"expects two trigger
  values and executes the corresponding order in the `orders` array when either of
  the trigger value is reached, the other order is lain dormant."* But it lives in
  the separate **GTT** API, as a broker-side resting trigger restricted to `LIMIT`
  orders. It is not an OCO on the order-placement path this matrix plans against, so
  the profile leaves `OrderType.OCO` out and the synthesizer emulates it. If your
  integration does speak the GTT API, register a custom profile — do not assume the
  default one is wrong.
- **Fractional**: not applicable; Indian cash equities trade in whole shares.

Sources: [Kite Connect v3 — Orders](https://kite.trade/docs/connect/v3/orders/),
[Kite Connect v3 — GTT](https://kite.trade/docs/connect/v3/gtt/).

## 4. Binance Spot

- **Order types** on `POST /api/v3/order`: `LIMIT`, `MARKET`, `STOP_LOSS`,
  `STOP_LOSS_LIMIT`, `TAKE_PROFIT`, `TAKE_PROFIT_LIMIT`, `LIMIT_MAKER`.
- **Iceberg**: *"Any `LIMIT` or `LIMIT_MAKER` type order can be made an iceberg order
  by sending an `icebergQty`"*, and *"Any order with an `icebergQty` MUST have
  `timeInForce` set to `GTC`."*
- **Trailing stop**: `trailingDelta`, combinable with `stopPrice` on the stop and
  take-profit types.
- **Order lists**: `POST /api/v3/orderList/oco`, `/oto`, `/otoco`. **OTOCO is a native
  bracket** — a working order that, once filled, activates a take-profit/stop-loss OCO
  pair. This is why the Binance profile carries `OrderType.BRACKET`.
- **OCO price geometry, quoted because the synthesizer enforces the same rule**: both
  legs must be on the same side, and for a SELL pair the take-profit price must exceed
  the last price, which must exceed the stop trigger; for a BUY pair the take-profit
  must be below the last price, which must be below the stop trigger.
- **TWAP is real but fenced.** It exists only on the separate **Algo** endpoints —
  `POST /sapi/v1/algo/spot/newOrderTwap` and `POST /sapi/v1/algo/futures/newOrderTwap`
  — with a duration of 300–86,400 seconds, a cap on concurrent algo orders, and, on
  spot, a notional band of roughly 1,000–100,000 USDT equivalent. An integration
  confined to `/api/v3/order` cannot fire it, and an order outside the notional band
  cannot use it either. Treat `OrderType.TWAP` on this profile as "available on the
  algo surface, subject to those bounds", and register a custom profile without it if
  your integration does not speak that surface.
- **VWAP**: not offered. `plan_order_execution(..., OrderType.VWAP, ...)` raises
  rather than quietly substituting TWAP.
- **Fractional**: base-asset quantities are decimal, bounded by the symbol's
  `LOT_SIZE` `stepSize` — a different concept from equity fractional shares.

Sources: [Binance Spot API — REST API](https://github.com/binance/binance-spot-api-docs/blob/master/rest-api.md),
[Binance Spot API — Trading endpoints](https://developers.binance.com/docs/binance-spot-api-docs/rest-api/trading-endpoints),
[Binance Algo — Spot TWAP](https://developers.binance.com/docs/algo/spot-algo/Time-Weighted-Average-Price-New-Order),
[Binance Algo — Futures TWAP](https://developers.binance.com/docs/algo/future-algo/Time-Weighted-Average-Price-New-Order).

## Order types this skill will not emulate

`EMULATABLE_ORDER_TYPES` is `{BRACKET, OCO, ICEBERG, TWAP}`. A request for anything
else that the broker does not support natively raises, and that is deliberate:

- **VWAP** needs a live intraday volume forecast. Emulating it as evenly spaced slices
  produces a TWAP wearing a VWAP label, which is a *different algorithm* benchmarked
  against a different number. Use `execution-algo-twap-vwap-slicing`.
- **PEGGED** needs a continuously re-evaluated reference price and repricing loop —
  see `peg-order-types-for-passive-execution` and
  `post-only-limit-repricing-under-fast-markets`.
- **TRAILING_STOP** needs a high-water mark maintained continuously against live
  quotes, and a broker that will not reject the resulting stream of amendments.
- **MOC / auction types** need the venue's auction mechanism; there is no local
  substitute. See `close-auction-participation-strategy`.

## Standardizing fallback emulation

Every emulated plan MUST:

1. State the initial native action in `primary_order_type` (`LIMIT` or `MARKET`), or
   `None` when there is nothing to fire immediately — which is the case for an
   emulated OCO, where both legs are conditional.
2. Carry a typed list of `EmulatedLeg` objects with explicit triggers, quantities and
   sides.
3. **Conserve quantity exactly**: `primary_quantity` plus the quantity of every
   scheduling leg equals the requested quantity, with no float residue.
4. Be executable by a local EMS that can watch price triggers, run interval timers,
   and **persist its state** — an emulated stop loss lost to a process restart is an
   unprotected position.

## Category

`broker-integration` — see top-level `mappings/` directory.
