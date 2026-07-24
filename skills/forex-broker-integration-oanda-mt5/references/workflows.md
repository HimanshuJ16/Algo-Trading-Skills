# Deep Workflow Reference — forex-broker-integration-oanda-mt5

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Practice vs. Live Credential Isolation:**
   - Maintain separate configuration objects and environment variables for Practice/Demo and Live accounts. Never use a shared client flag.

2. **Pip & Pipette Precision Handling:**
   - Use `ForexPipEngine` to determine exact pip sizes across standard pairs ($10^{-4}$), JPY pairs ($10^{-2}$), and metals ($10^{-1}$).
   - Convert price differences into pips via `price_diff_to_pips(pair, diff)`.
   - Calculate exact Pip value in base account currency via `calculate_pip_value()`.

3. **Lot-Size to Units Conversion:**
   - Translate lot sizes to units using `LOT_SIZES` (`standard` = 100,000, `mini` = 10,000, `micro` = 1,000, `nano` = 100).

4. **Overnight Swap & Wednesday Triple-Swap Rule:**
   - Model overnight financing rates using `SwapRolloverCalculator`.
   - Enforce the **Wednesday 3x Triple-Swap Rule** (Wednesday rollover accounts for weekend holding costs).

5. **MetaTrader 5 (MT5) Bridge Liveness Monitoring:**
   - Run `MT5BridgeMonitor` to independently verify underlying MT5 terminal connection state (`terminal_info().connected`).

6. **Forex 24/5 Market Gap Scheduling:**
   - Adapt risk resets across Friday 22:00 UTC market close to Sunday 22:00 UTC market open boundaries.

## Failure Modes Observed in Production

- **JPY Pip Precision Bug:** Assuming 4-decimal pips ($0.0001$) for JPY pairs ($0.01$), miscalculating stop-losses and position sizing by a factor of 100x.
- **Lot Size Confusion:** Confusing standard lots (100k) with micro lots (1k), resulting in 100x oversized exposure.
- **Omitted Overnight Swaps:** Ignoring negative rollover interest in backtests, overstating long-term carry strategy performance.
- **Silent MT5 Terminal Disconnect:** Python bridge remaining active while MT5 terminal loses broker connection, producing stale order states.

## Production Implementation Reference

- Reference code: `scripts/pip_conversion.py` (`ForexPipEngine`, `SwapRolloverCalculator`, `MT5BridgeMonitor`, `lots_to_units`).
- Automated unit tests: `scripts/test_pip_conversion.py`.
