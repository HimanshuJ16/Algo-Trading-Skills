# Pre-Flight Checklist — Perpetual Futures Funding Rate Handling

## Contract scope
- [ ] Is this a **linear** (USDT/USDC-margined) perpetual? Inverse/COIN-M uses a different notional formula and settles in the base coin.
- [ ] Does the venue settle funding **discretely** at a timestamp (Binance/Bybit/OKX), or **continuously** (Deribit)? The per-interval payment is an upper bound on a continuous venue.

## Inputs
- [ ] Is `funding_rate` a per-interval **decimal** (`0.0001` = `+0.01%`), verified at the API boundary rather than assumed?
- [ ] Is `funding_interval_hours` read from the venue **for this symbol and this settlement** — not defaulted to 8?
- [ ] Is `mark_price` the mark at the funding timestamp, not the last trade price and not the entry price?
- [ ] Is direction resolved from an explicit `LONG`/`SHORT`, with Binance one-way `positionSide="BOTH"` converted from the sign of `positionAmt` first?
- [ ] Do the position symbol and the funding print symbol match?

## Calculation
- [ ] Notional = `|position_qty| × mark_price`?
- [ ] Is the payment signed from the **position's** side (long pays on `F > 0`, short receives), not from the rate's sign?
- [ ] Are the APR and APY computed off `8760 / interval_hours` — and is it understood that they extrapolate one print, not forecast a year?
- [ ] Is the right one being quoted: simple APR for a single held interval, compounded APY only for a genuinely rolled carry?

## Audit and policy
- [ ] Is `max_adverse_funding_apr` set deliberately, with the strict (`>`) boundary understood?
- [ ] Does the audit correctly treat funding income as never breaching?
- [ ] Is `recommended_action` understood as advisory only, with any actual unwind routed through an independent risk control?

## Timing
- [ ] Is it understood that these venues do **not** prorate — the whole interval is charged to whoever holds the position at the timestamp?
- [ ] Is `next_funding_timestamp_utc` fresh (non-negative `hours_to_next_funding`) rather than a stale snapshot?
- [ ] Is any exit-before-funding plan tolerant of Binance's documented ~15s settlement deviation?

## Failure handling
- [ ] Are non-finite rates rejected rather than classified as income?
- [ ] Are non-positive intervals rejected rather than coerced?
- [ ] Are funding costs included in the backtest that justified this position?
