# Workflows — mt5-python-bridge-for-forex-bots

Full procedure for submitting one `TRADE_ACTION_DEAL` through the MT5 Python bridge.

## 1. Establish the terminal adapter

1. Confirm the host is Windows x86-64 with a running, logged-in MT5 terminal — the
   `MetaTrader5` distribution publishes `win_amd64` wheels only.
2. Confirm **Algorithmic Trading is enabled in the terminal itself**. If it is not, every
   deal returns `10027 TRADE_RETCODE_CLIENT_DISABLES_AT` with a perfectly valid request.
3. Wrap `MetaTrader5` in an object exposing `order_send(request_dict)` and
   `symbol_info(symbol)`. The engine never imports the package directly, so the same code
   path is exercised in tests with a stub adapter.
4. Build `MT5Config`. `magic_number` must be positive and unique to this strategy; the engine
   raises `MT5BridgeError` on `0` because a zero magic cannot be told apart from a manual
   trade during reconciliation. `password` is excluded from the dataclass `repr`.

## 2. Resolve the symbol's trading conditions

1. Call `get_symbol_spec(symbol)`, which wraps `symbol_info()` and normalises the namedtuple
   into `MT5SymbolSpec`.
2. `None` means the terminal does not know the symbol. Common cause: the broker's suffix
   (`EURUSD.pro`, `EURUSDm`, `EURUSD.raw`). Confirm the exact name and that it is selected in
   Market Watch (`symbol_select`). Do **not** substitute defaults — the engine returns
   `MT5_SYMBOL_UNAVAILABLE` and submits nothing.
3. In `dry_run` mode there is no terminal, so pass `symbol_spec=` explicitly. A supplied
   spec whose `symbol` differs from the order's is rejected as `MT5_SYMBOL_MISMATCH`: the
   request is serialised from the spec's name, so a mismatch would route the deal to a
   different instrument.

## 3. Validate the intent locally

Run in this order, short-circuiting on the first failure. Every rejection here returns
`retry_disposition = NOT_SENT` and an empty `mql_trade_request` — nothing reached the server.

1. **Side** — `BUY` or `SELL` only. `MT5_INVALID_ORDER_TYPE` otherwise. Never fall through to
   a default side.
2. **Price** — positive and finite. `MT5_INVALID_PRICE` otherwise.
3. **Volume** — against `volume_min`, `volume_max`, `volume_limit` and `volume_step`, all read
   from the broker. The step check compares `round(v / step) * step` to `v` with a
   `step * 1e-6` tolerance, so IEEE-754 noise (`0.07 / 0.01 == 6.999999999999999`) does not
   reject a legitimate order.
4. **Stops** — side first (Buy: SL below, TP above; Sell: the reverse), then distance against
   `trade_stops_level` in points, with a sub-point tolerance so a stop placed exactly at the
   limit is accepted. `0.0`/`None` means "no level set" and skips both checks.
5. **Filling mode** — `resolve_filling_mode()` reads the `SYMBOL_FILLING_MODE` bitmask and
   returns the matching `ENUM_ORDER_TYPE_FILLING` value, preferring `config.preferred_filling`
   and falling back to the other. `None` (neither FOK nor IOC permitted) → `MT5_INVALID_FILLING`,
   refused locally. A mask of `0` means the terminal published nothing; the preference is sent
   unverified and a warning is logged.

## 4. Serialise the MqlTradeRequest

Volume is normalised to the symbol's step and emitted as a `float`. `price`, `sl` and `tp` are
rounded to the symbol's `digits`. `type_filling` is the resolved value, never a hard-coded
constant.

```python
{
    "action": TRADE_ACTION_DEAL,   # 1
    "symbol": spec.symbol,
    "volume": 0.1,                 # float, on volume_step
    "type": ORDER_TYPE_BUY,        # 0 / 1, from the validated side
    "price": 1.08500,              # current Ask (Buy) or Bid (Sell), rounded to digits
    "sl": 1.08000,                 # 0.0 when unset
    "tp": 1.09500,                 # 0.0 when unset
    "deviation": 10,
    "type_filling": ORDER_FILLING_IOC,   # 1, derived from the symbol
    "magic": 234000,
    "comment": "Python_Algo_Bot",
}
```

If `dry_run=True`, stop here: the report carries the serialised request with
`status = MT5_DRY_RUN_VALIDATED` and `is_executed=False`. A dry run never claims a fill.

## 5. Submit once and classify

`execute_forex_order` calls `order_send` exactly once. Three failure shapes are handled
identically because they are the same problem — the client lost the answer:

- the adapter raised,
- `order_send()` returned `None`,
- the retcode is `10011` / `10012` / `10028` / `10031`.

All three produce `status = MT5_EXECUTION_AMBIGUOUS`, `requires_reconciliation = True`, and an
audit note naming the magic number to reconcile on.

Otherwise:

| Retcode | Report |
|---|---|
| `10009` | `MT5_ORDER_EXECUTED_SUCCESS`, `is_executed=True`, fill volume and price taken from the result; if the result omits either, the requested value is used and a warning logged, since 10009 means the request completed in full |
| `10010` | `MT5_ORDER_PARTIALLY_FILLED`, `is_executed=True`, `filled_volume_lots` < `volume_lots`. A `10010` carrying no confirmed volume is self-contradictory and is downgraded to AMBIGUOUS |
| `10008` | `MT5_ORDER_PLACED`, `is_executed=False`, `order_id` retained |
| `10004`/`10020`/`10021`/`10024` | `MT5_EXECUTION_FAILED`, `retry_disposition=RETRYABLE` |
| anything else | `MT5_EXECUTION_FAILED`, `retry_disposition=TERMINAL` |

## 6. Act on the disposition

```
COMPLETE   -> done. For 10010, size any top-up from (requested - filled), never from requested.
RETRYABLE  -> re-quote from a fresh tick and resend, under a bounded attempt cap.
TERMINAL   -> do not resend this request. Fix it, or stand down.
AMBIGUOUS  -> reconcile, then decide. Never resend first.
NOT_SENT   -> rejected locally; nothing happened.
```

### Reconciliation procedure for AMBIGUOUS

1. Query `history_deals_get(date_from, date_to)` over a window covering the submission, and
   `positions_get()`.
2. Filter on `deal.magic == config.magic_number` and the symbol.
3. If a matching deal exists for the intended side and volume, the order landed — record the
   ticket and do not resend.
4. If none exists, the order did not land and a resend is safe.
5. If the terminal is unreachable and step 1 cannot complete, **stop trading that symbol** and
   escalate. An unbounded retry loop against an ambiguous order state is how a bot ends up
   with several times its intended exposure.

Do not use `comment` as a client order id in step 2: it is short and the trade server may
overwrite it. `magic` is the only durable tag MT5 offers.

## 7. Audit report

`MT5OrderReport` carries: both tickets (`order_id`, `deal_id`), the requested
(`volume_lots`) and confirmed (`filled_volume_lots`) sizes, the broker-confirmed
`execution_price`, the raw `retcode` and `broker_comment`, the serialised
`mql_trade_request`, the `retry_disposition`, and `requires_reconciliation`. Persist the whole
report — the serialised request is what lets a post-incident review reconstruct exactly what
was sent.
