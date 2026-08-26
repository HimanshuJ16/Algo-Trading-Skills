# Pre-Flight / Sign-off Checklist — mt5-python-bridge-for-forex-bots

Use this before pointing the bridge at a funded account.

## Terminal & Configuration

- [ ] **Host is Windows x86-64** with a running, logged-in MT5 terminal. The `MetaTrader5`
      wheel is `win_amd64` only.
- [ ] **Algorithmic Trading is enabled in the terminal.** If not, every deal returns `10027`
      with a valid request.
- [ ] **Adapter injected.** `MT5PythonBridgeEngine` was constructed with a terminal adapter,
      or with `dry_run=True` — never in a mode that fabricates a fill.
- [ ] **Magic number is positive and unique to this strategy.** It is the only tag available
      for reconciling an ambiguous submission.
- [ ] **Password is not in any log line.** `repr(MT5Config(...))` was checked.

## Symbol Metadata

- [ ] **Symbol name matches the broker exactly**, suffix included (`.pro`, `m`, `.raw`), and
      is selected in Market Watch. `symbol_info()` returning `None` blocks the order rather
      than falling back to defaults.
- [ ] **No lot constant is hard-coded.** `volume_min`, `volume_max`, `volume_step` and
      `volume_limit` all come from `symbol_info()`.
- [ ] **Filling mode is derived, not assumed.** `type_filling` comes from the
      `SYMBOL_FILLING_MODE` bitmask, and the code does not confuse the mask numbering
      (FOK=1, IOC=2) with the enum numbering (FOK=0, IOC=1).
- [ ] **Prices are rounded to the symbol's `digits`** before submission.

## Order Validation

- [ ] **Side is whitelisted.** Anything other than `BUY`/`SELL` is rejected; no `else` branch
      turns an unrecognised side into the opposite order.
- [ ] **Both SL and TP directions are validated**, for both sides.
- [ ] **Stop distance is checked against `trade_stops_level`** with a sub-point tolerance, and
      `trade_stops_level == 0` is not read as "any distance is allowed".
- [ ] **The submitted price is a current quote** — Ask for a Buy, Bid for a Sell — not a
      closed bar's close.

## Execution & Retry Safety

- [ ] **Result is read from `MqlTradeResult`**, not echoed from the request:
      `result.volume` and `result.price` are the broker-confirmed values.
- [ ] **`None` result is handled** before any attribute or `.get()` access.
- [ ] **`10010 DONE_PARTIAL` is treated as a fill.** A position is open; follow-ups are sized
      from the shortfall.
- [ ] **`10008 PLACED` retains the ticket** and claims no exposure.
- [ ] **Ambiguous outcomes are flagged, not retried.** Adapter exception, `None` result, and
      retcodes `10011`/`10012`/`10028`/`10031` all set `requires_reconciliation=True`.
- [ ] **Reconciliation runs before any resend**, filtering `history_deals_get` /
      `positions_get` on the magic number — and the `comment` field is not used as a client
      order id.
- [ ] **Retry loops are bounded**, and an unrecognised retcode is treated as terminal, never
      as retryable.
- [ ] **Every submission is logged with the serialised request**, both tickets, the retcode
      and the disposition.

## Testing

- [ ] `python -m unittest discover -s skills/mt5-python-bridge-for-forex-bots/scripts` passes.
- [ ] A dry run against the intended symbol produced a request whose `volume`, `type_filling`
      and rounded prices were inspected by a human before live enablement.
- [ ] The strategy ran on a demo account for a defined period with partial fills and at least
      one rejection observed and handled.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Account type tested (demo / live): ___________________________
- Broker & server: ___________________________
- Netting or hedging account? ___________________________
- Magic number assigned: ___________________________
