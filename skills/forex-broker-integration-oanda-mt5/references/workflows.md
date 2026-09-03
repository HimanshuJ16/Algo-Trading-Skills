# Deep Workflow Reference — forex-broker-integration-oanda-mt5

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Practice vs. Live Credential Isolation:**
   - Maintain separate configuration objects and environment variables for Practice/Demo and Live accounts. Never use a shared client flag.
   - OANDA serves the two environments from entirely separate host families — REST `api-fxpractice.oanda.com` vs `api-fxtrade.oanda.com`, streaming `stream-fxpractice.oanda.com` vs `stream-fxtrade.oanda.com` — with separate account IDs and separate API tokens. `oanda_hosts(environment)` exposes both sets and refuses to default, so the environment must be named explicitly at construction.

2. **Pip & Pipette Precision Handling:**
   - Source pip size from **broker instrument metadata**, not from the instrument's name:
     - OANDA: `GET /v3/accounts/{accountID}/instruments` returns `pipLocation` per instrument; the pip is at `10 ^ pipLocation` (`-4` for EUR/USD, `-2` for USD/JPY, and `0` for some CFDs). Build an `InstrumentSpec` with `InstrumentSpec.from_oanda_instrument()`.
     - MT5: `symbol_info(symbol).digits` gives the quote precision. By the fractional-pricing convention, 5- and 3-digit quotes are priced in pipettes so the pip sits one decimal left of the last digit; 4-, 2- and 1-digit quotes price directly in pips. `InstrumentSpec.from_mt5_symbol_info()` applies that rule — verify it against the broker's contract specification for non-FX symbols.
   - `ForexPipEngine.pip_size(pair, spec)` uses the spec when given. Without one it falls back to `infer_pip_location()`, which handles only currency pairs whose legs are both in `INFERABLE_CURRENCIES` (JPY-quoted → 2nd decimal, otherwise 4th), logs a warning, and **raises for metals, index and crypto CFDs** rather than inventing a pip size for them.
   - Convert price differences into pips via `price_diff_to_pips(pair, diff, spec)`.
   - Value a pip in the account currency via `calculate_pip_value()`. When the instrument's quote currency differs from the account currency, a `quote_to_account_fx_rate` is **required** — the function raises rather than assuming 1.0.

3. **Lot-Size to Units Conversion:**
   - Translate lot sizes to units using `LOT_SIZES` (`standard` = 100,000, `mini` = 10,000, `micro` = 1,000, `nano` = 100). Confirm these against the broker's own contract size before sizing orders; they are the common retail convention, not a universal one.

4. **Overnight Swap & the Triple-Swap Rollover:**
   - Supply the broker's published swap rates to `SwapRolloverCalculator`; it has no defaults and raises for an unconfigured pair or side rather than accruing an assumed rate. Note the units your broker quotes in (account currency per lot, or points) and convert before passing them in.
   - Triple swap is a **value-date effect, not a fixed weekday**. A position held past the rollover whose value date jumps Friday → Monday accrues three days of financing. For T+2 instruments that is the Wednesday rollover; for T+1 instruments — USD/CAD, USD/TRY, USD/RUB, USD/PHP by market convention — it is the Thursday rollover. `triple_swap_weekday(pair, settlement_days)` encodes this, with an explicit override for brokers whose convention differs.
   - Count triple-swap rollovers rather than flagging them: a multi-week hold crosses one per week. `count_triple_swap_rollovers(first, last, weekday)` returns the count for `calculate_swap(..., triple_swap_days=n)`.

5. **MetaTrader 5 (MT5) Bridge Liveness Monitoring:**
   - Run `MT5BridgeMonitor` to verify the underlying MT5 terminal's connection state independently of the Python process's own health. The check function is a required constructor argument — a monitor that defaults to "healthy" reports success for a terminal it never probed.
   - Use `mt5_terminal_connected_check(mt5)` rather than reading `terminal_info().connected` directly: `terminal_info()` returns `None` when no terminal is attached, so the direct attribute access raises `AttributeError` in exactly the failure case being monitored. The helper also requires `trade_allowed`, since a reachable terminal with algorithmic trading switched off cannot execute.

6. **Forex 24/5 Market Gap Scheduling:**
   - The weekly close/open boundary is defined in **broker-server time**, and brokers differ. The common retail convention is a Friday close and Sunday open around 17:00 America/New_York.
   - Do not hard-code that boundary as a UTC constant: 17:00 New York is 21:00 UTC during US daylight saving time and 22:00 UTC outside it, and the US and EU DST transitions do not fall on the same dates. Resolve the boundary through a timezone database against the broker's stated server timezone. See `daylight-saving-time-transition-handling` and `multi-timezone-session-scheduling`.

## Known Failure Modes

- **Pip Size Guessed From the Instrument Name:** Inferring pip size from the ticker rather than reading the broker's `pipLocation`/`digits`. Fails for JPY pairs against a 4-decimal assumption (100x), and fails for metals, index and crypto CFDs in whichever direction the guess happens to fall.
- **Pip Value Without Currency Conversion:** Treating pip value in the quote currency as if it were in the account currency. For a USD account trading USD/JPY this overstates pip value by roughly the USD/JPY rate (~150x) and mis-sizes every risk-per-trade calculation downstream.
- **Lot Size Confusion:** Confusing standard lots (100k) with micro lots (1k), resulting in 100x oversized exposure.
- **Omitted or Assumed Overnight Swaps:** Ignoring negative rollover interest in backtests, overstating long-term carry strategy performance — or, worse, substituting a plausible-looking assumed rate for the broker's published one.
- **Triple Swap Modelled as a Boolean:** Charging one triple-swap rollover for a hold of any length, understating financing on multi-week positions; and assuming Wednesday for T+1 instruments such as USD/CAD, charging it a day early.
- **Silent MT5 Terminal Disconnect:** Python bridge remaining active while the MT5 terminal loses its broker connection, producing stale order states.

## Production Implementation Reference

- Reference code: `scripts/pip_conversion.py` (`InstrumentSpec`, `ForexPipEngine`, `SwapRolloverCalculator`, `MT5BridgeMonitor`, `oanda_hosts`, `lots_to_units`).
- Automated unit tests: `scripts/test_pip_conversion.py`.
