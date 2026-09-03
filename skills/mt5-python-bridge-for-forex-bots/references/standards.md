# Broker & Framework Coverage — mt5-python-bridge-for-forex-bots

## Scope

`TRADE_ACTION_DEAL` (market deal) submission only. Pending orders, stop modification,
position close and close-by use different `MqlTradeRequest` field sets and are out of scope.

## MqlTradeRequest fields used

Under Market Execution, MQL5 states the request "requires to specify the following 5 fields:
`action`, `symbol`, `volume`, `type`, `type_filling`". The remaining fields below are optional
but sent deliberately.

| Field | Value used here | Note |
|---|---|---|
| `action` | `TRADE_ACTION_DEAL` (1) | Market deal only |
| `symbol` | broker symbol name from `symbol_info().name` | Suffixes (`.pro`, `m`) are broker-specific |
| `volume` | `float`, normalised to `volume_step` | Must be a float, never an int |
| `type` | `ORDER_TYPE_BUY` (0) / `ORDER_TYPE_SELL` (1) | Rejected locally if the side is anything else |
| `price` | current quote, rounded to `digits` | Ask for Buy, Bid for Sell. "Market orders of symbols, whose execution type is 'Market Execution'… do not require specification of price" |
| `sl` / `tp` | rounded to `digits`, `0.0` = unset | Both directions validated |
| `deviation` | `config.max_slippage_points` | "The maximal price deviation, specified in points" |
| `type_filling` | derived from `SYMBOL_FILLING_MODE` | See the two-enumeration table below |
| `magic` | `config.magic_number`, must be > 0 | The only reconciliation tag available |
| `comment` | free text | Short, and the server may overwrite it — not a client order id |

## The two filling enumerations

These are the same concepts under two different numberings. Conflating them is the usual
cause of retcode `10030`.

| Mode | `SYMBOL_FILLING_MODE` (bitmask, from `symbol_info().filling_mode`) | `ENUM_ORDER_TYPE_FILLING` (value sent in `type_filling`) |
|---|---|---|
| FOK | `SYMBOL_FILLING_FOK` = 1 | `ORDER_FILLING_FOK` = 0 |
| IOC | `SYMBOL_FILLING_IOC` = 2 | `ORDER_FILLING_IOC` = 1 |
| BOC | `SYMBOL_FILLING_BOC` = 4 | build-dependent; limit/stop-limit orders only |
| RETURN | — | build-dependent; disabled under Market Execution |

Only FOK and IOC are candidates for a market deal, so this module defines only those two
`ENUM_ORDER_TYPE_FILLING` values. The numeric values published for BOC and RETURN differ
between MQL5 builds and are deliberately **not** asserted here.

## Symbol properties consulted

| Property | Official wording | Use |
|---|---|---|
| `volume_min` | "Minimal volume for a deal" | Lower bound |
| `volume_max` | "Maximal volume for a deal" | Upper bound |
| `volume_step` | "Minimal volume change step for deal execution" | Step check + normalisation |
| `volume_limit` | Maximum allowed aggregate volume in one direction | Checked when > 0 |
| `digits` | "Digits after a decimal point" | Price/SL/TP rounding |
| `point` | "Symbol point value" | Converts a price distance to points |
| `trade_stops_level` | "Minimal indention in points from the current close price to place Stop orders" | SL/TP distance check |
| `trade_freeze_level` | "Distance to freeze trade operations in points" | Captured but **not** enforced: it governs modifying or closing an existing order/position, not opening a market deal |
| `filling_mode` | `SYMBOL_FILLING_MODE` bitmask | `type_filling` selection |

`symbol_info()` returns `None` on error, which is how an unknown or unselected symbol
presents itself.

## Retcode dispositions

| Retcode | MQL5 name / description | Disposition here |
|---|---|---|
| 10008 | `TRADE_RETCODE_PLACED` — "Order placed" | COMPLETE, `is_executed=False`, ticket retained |
| 10009 | `TRADE_RETCODE_DONE` — "Request completed" | COMPLETE, filled |
| 10010 | `TRADE_RETCODE_DONE_PARTIAL` — "Only part of the request was completed" | COMPLETE, **filled** at `result.volume` |
| 10004 | `TRADE_RETCODE_REQUOTE` — "Requote" | RETRYABLE |
| 10020 | `TRADE_RETCODE_PRICE_CHANGED` — "Prices changed" | RETRYABLE |
| 10021 | `TRADE_RETCODE_PRICE_OFF` — "There are no quotes to process the request" | RETRYABLE |
| 10024 | `TRADE_RETCODE_TOO_MANY_REQUESTS` — "Too frequent requests" | RETRYABLE |
| 10011 | `TRADE_RETCODE_ERROR` — "Request processing error" | AMBIGUOUS |
| 10012 | `TRADE_RETCODE_TIMEOUT` — "Request canceled by timeout" | AMBIGUOUS |
| 10028 | `TRADE_RETCODE_LOCKED` — "Request locked for processing" | AMBIGUOUS |
| 10031 | `TRADE_RETCODE_CONNECTION` — "No connection with the trade server" | AMBIGUOUS |
| 10013 | `TRADE_RETCODE_INVALID` — "Invalid request" | TERMINAL |
| 10014 | `TRADE_RETCODE_INVALID_VOLUME` | TERMINAL |
| 10015 | `TRADE_RETCODE_INVALID_PRICE` | TERMINAL |
| 10016 | `TRADE_RETCODE_INVALID_STOPS` | TERMINAL |
| 10017 | `TRADE_RETCODE_TRADE_DISABLED` | TERMINAL |
| 10018 | `TRADE_RETCODE_MARKET_CLOSED` | TERMINAL |
| 10019 | `TRADE_RETCODE_NO_MONEY` | TERMINAL |
| 10026 | `TRADE_RETCODE_SERVER_DISABLES_AT` — "Autotrading disabled by server" | TERMINAL |
| 10027 | `TRADE_RETCODE_CLIENT_DISABLES_AT` — "Autotrading disabled by client terminal" | TERMINAL |
| 10030 | `TRADE_RETCODE_INVALID_FILL` — "Invalid order filling type" | TERMINAL |
| 10034 | `TRADE_RETCODE_LIMIT_VOLUME` | TERMINAL |
| *unrecognised* | — | TERMINAL (never RETRYABLE) |

An adapter exception and `order_send()` returning `None` are both classified AMBIGUOUS.

Note: `10013`'s official MQL5 name is `TRADE_RETCODE_INVALID`, not `TRADE_RETCODE_INVALID_REQUEST`.
The module keeps the old spelling as an alias for backward compatibility.

## Result fields consulted

`MqlTradeResult.volume` is "Deal volume, confirmed by broker. It depends on the order filling
type", and `MqlTradeResult.price` is "Deal price, confirmed by broker." Both are read from the
result rather than echoed from the request. `order` and `deal` are distinct tickets and both
are recorded.

## Idempotency

MT5's `order_send()` accepts **no client-assigned order id**. `magic` (`ulong`, per-EA) is the
only durable tag, and it appears on deals returned by `history_deals_get()` alongside `order`,
`position_id`, `volume` and `price` — which is what makes reconciliation-by-magic workable.
`comment` is not a substitute: it is short and the trade server may overwrite it.

Consequently this engine submits once and never retries. Retry is the caller's decision, and
for an AMBIGUOUS disposition it must be preceded by a `history_deals_get` / `positions_get`
lookup on the magic number.

## Platform constraint

The `MetaTrader5` PyPI distribution publishes `win_amd64` wheels only (Python 3.6–3.14) and
requires a running, logged-in terminal on the same host. This module therefore does not import
it; the terminal is injected as an adapter.

## Sources

| Claim | Source |
|---|---|
| Full `TRADE_RETCODE_*` numbering and official descriptions, incl. 10008/10009/10010/10013/10030/10031 | MQL5 — Trade Server Return Codes, https://www.mql5.com/en/docs/constants/errorswarnings/enum_trade_return_codes |
| `MqlTradeRequest` field descriptions; five required fields under Market Execution; price not required for Market Execution symbols | MQL5 — MqlTradeRequest, https://www.mql5.com/en/docs/constants/structures/mqltraderequest |
| `MqlTradeResult.volume` / `.price` "confirmed by broker"; distinct `order` and `deal` tickets | MQL5 — MqlTradeResult, https://www.mql5.com/en/docs/constants/structures/mqltraderesult |
| Filling-mode semantics; allowed modes read from `SYMBOL_FILLING_MODE` as a combination of flags; BOC limited to limit/stop-limit; RETURN disabled under Market Execution | MQL5 — Order Properties, https://www.mql5.com/en/docs/constants/tradingconstants/orderproperties |
| `SYMBOL_FILLING_FOK` = 1, `SYMBOL_FILLING_IOC` = 2, `SYMBOL_FILLING_BOC` = 4; `SYMBOL_VOLUME_*`, `SYMBOL_TRADE_STOPS_LEVEL`, `SYMBOL_TRADE_FREEZE_LEVEL`, `SYMBOL_DIGITS`, `SYMBOL_POINT` wording | MQL5 — Symbol Properties, https://www.mql5.com/en/docs/constants/environment_state/marketinfoconstants |
| `ORDER_FILLING_FOK` = 0, `ORDER_FILLING_IOC` = 1 | MQL5 Programming Book — Order execution modes by price and volume, https://www.mql5.com/en/book/automation/experts/experts_execution_filling |
| `order_send()` returns an `MqlTradeResult`; error detail via `last_error()`; "successful sending of a request does not entail that the requested trading operation will be executed successfully" | MQL5 — order_send / order_check (Python), https://www.mql5.com/en/docs/python_metatrader5/mt5ordersend_py and https://www.mql5.com/en/docs/python_metatrader5/mt5ordercheck_py |
| `symbol_info()` returns a namedtuple, "Return None in case of an error"; field list incl. `volume_min/max/step`, `trade_stops_level`, `filling_mode`, `digits`, `point` | MQL5 — symbol_info (Python), https://www.mql5.com/en/docs/python_metatrader5/mt5symbolinfo_py |
| Deals carry `magic`, `order`, `position_id`, `volume`, `price`, `comment`; `history_deals_get()` returns None on error | MQL5 — history_deals_get (Python), https://www.mql5.com/en/docs/python_metatrader5/mt5historydealsget_py |
| `win_amd64` wheels only; Python >=3.6,<4 | PyPI — MetaTrader5, https://pypi.org/project/MetaTrader5/ |
| Unsupported filling mode surfaces as retcode 10030 with the comment "Unsupported filling mode"; fix is to read the symbol's permitted modes | MQL5 forum — "Python / MT5 — no order possible — error message 'Unsupported filling mode'", https://www.mql5.com/en/forum/368425 (vendor-hosted community thread; corroborates the official 10030 definition with observed behaviour) |

**Not verified, and therefore not asserted anywhere in this skill:**

- The exact maximum length of an MT5 order comment. It is short and server-modifiable; the
  module warns above 31 characters and the documentation says only "do not rely on it",
  without claiming a specific cap.
- The numeric values of `ORDER_FILLING_BOC` / `ORDER_FILLING_RETURN`, which sources disagree
  on across MQL5 builds. Neither is usable for a market deal, so neither is defined.
- Whether `order_send()` ever returns `None` is not stated in the MQL5 Python reference (which
  documents only the `MqlTradeResult` return). The `None` path is handled because the sibling
  Python API calls (`symbol_info`, `history_deals_get`) are documented to return `None` on
  error and community reports describe the same for `order_send`; the handling is defensive
  and costs nothing if it never fires.
- Broker-side floating stop levels. `trade_stops_level == 0` is treated as "no static minimum
  published", not as "any distance is acceptable".

## Regulatory & Operational Notes

No jurisdiction-specific regulatory requirement is asserted by this skill. The duplicate-order
hazard it guards against — resending after an ambiguous response — is the operational form of
the pre-trade control expectations in SEC Rule 15c3-5 and MiFID II RTS 6, but neither rule is
being applied here to a retail MT5 terminal; the control is justified on its own engineering
merits.
