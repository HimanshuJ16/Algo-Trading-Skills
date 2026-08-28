# Deep Workflow Reference — stress-testing-against-historical-crash-scenarios

## Full Procedure

### 1. Build the crash scenario library

Each `CrashScenario` carries `name`, `description`, `asset_returns`
(`symbol -> cumulative return`), and the provenance fields `window_start`, `window_end`,
`basis` and `calibration_note`.

- **Date every scenario and state its basis.** A magnitude without a window and a price
  basis cannot be checked, compared or reproduced. Peak-to-trough close-to-close, single-day
  intraday against the prior close, and calendar-window return are three different
  quantities; a library mixing them without saying so invites a false ranking.
- **Decide window return vs. adverse move, per asset, and be consistent.** Applying the
  equity index's window to every leg understates whichever leg bottomed on different
  dates. Gold's Feb–Mar 2020 window return was about −3.8%; its adverse move inside the
  episode was roughly −12%.
- **Include a `DEFAULT` fallback if — and only if — you want unnamed symbols stressed by
  assumption.** Symbols priced off it are reported in `fallback_symbols`. Without a
  `DEFAULT`, unnamed symbols are reported in `unshocked_symbols` and contribute $0; the
  engine does not invent a shock for them.
- **Source single-name shocks from point-in-time data.** No single-name shocks ship with
  the library. Check every symbol's listing date against `window_start` — a shock for a
  pre-IPO period is fabricated, not conservative.
- Constructor-time validation rejects: an empty library, a duplicate scenario name, a
  scenario with no shocks, a non-finite shock, a shock below −1.0, a blank scenario name,
  and anything that is not a `CrashScenario`.

### 2. Replay the current positions

For each scenario, for each held, non-flat, priced symbol:

$$\text{pnl}_i = q_i \cdot p_i \cdot R_{i,s}, \qquad
\Delta\text{NAV}_s = \sum_i \text{pnl}_i, \qquad
\text{pct}_s = \frac{\Delta\text{NAV}_s}{\text{NAV}}$$

$q_i$ is **signed** — a short is negative — so a short position gains under a negative
shock. `NAV` is the capital base, not net exposure.

Input validation raises `ValueError` on: a non-finite or non-positive NAV; a non-finite,
non-numeric or bool quantity or price; an empty `positions` map; a blank or `DEFAULT`
position key; non-dict `positions` or `prices`; a book in which no held position could be
priced; and a stressed P&L that overflows to infinity (individually finite inputs can still
overflow, and an infinite loss would otherwise format as `inf% loss ($inf)`).

Quantities and prices may be any `float`-convertible numeric — `Decimal` and `numpy`
scalars included — so values coming straight off the data layer need no conversion at the
call site. Bools, strings and bytes are rejected: a numeric string is a parsing bug
upstream, not a quantity.

### 3. Read coverage before reading P&L

| Field | Meaning |
|---|---|
| `status` | `STRESS_TEST_COMPLETE`, or `STRESS_TEST_INCOMPLETE_COVERAGE` if any field below is non-empty |
| `unpriced_symbols` | Held, non-flat symbols with no price — excluded from **every** scenario |
| `unshocked_symbols` | Held symbols no scenario names, with no `DEFAULT` to fall back to |
| `fallback_symbols` | Held symbols priced off a scenario `DEFAULT`, not a symbol-specific return |
| `ScenarioResult.shocked_value_usd` | Gross absolute position value that actually received a shock |

A $0 stressed loss means either nothing moved or nothing matched. Incomplete coverage means
the reported loss **understates** the book — it is reported, not rejected, because partial
coverage is a legitimate state (BCBS d450 Principle 4 requires the exclusion be explained
and documented, not that it be forbidden). Flat positions are not a coverage gap: a symbol
held at zero quantity is skipped without being counted unpriced.

### 4. Identify the worst case

$$s^{*} = \arg\min_s \text{pct}_s$$

on the **signed** percentage. `worst_loss_pct = max(0, -\text{pct}_{s^*})` and
`worst_loss_usd` likewise — loss magnitudes floored at zero. `worst_pnl_pct` and
`worst_pnl_usd` carry the same outcome signed, so a scenario in which the book *gains*
remains visible in the report rather than being rendered as a loss.

### 5. Enforce the gate

`threshold_breached = worst_loss_pct >= max_stressed_loss_pct`, evaluated on the unrounded
figure. At-limit is a breach. `breach_reason` carries the scenario name, the loss
percentage and the dollar loss, and is logged at WARNING alongside any coverage warnings.

A scenario gain can never fire the gate.

### 6. Archive

`results` carries the per-scenario breakdown — signed P&L, percentage, per-asset impact,
the gross value actually shocked, and the symbols that were not — so any aggregate traces
back to its drivers and a reviewer can see the replay's reach, not just its number.

## Migration from version 1.0.0

Five behaviours changed. Each was a defect; all five are pinned by regression tests.

| Behaviour | 1.0.0 | 2.0.0 |
|---|---|---|
| Worst-case is a **gain** | `abs()` reported it as a loss of the same size and could fire the gate on a profitable book | `worst_loss_pct` is `0.0`; `worst_pnl_*` carries the signed gain |
| Non-finite quantity/price/NAV | NaN propagated; `nan >= limit` is False, so the report read `threshold_breached=False`, `breach_reason=None` | raises `ValueError` |
| Held position absent from `prices` | skipped with `continue`, contributing $0, unreported | reported in `unpriced_symbols`; raises if **nothing** could be priced |
| Symbol in neither the scenario nor its `DEFAULT` | hard-coded −0.30 applied | reported in `unshocked_symbols`, contributes $0 |
| Empty `positions` | returned a $0 loss, no breach, first scenario as "worst" | raises `ValueError` |

`2008_GFC` no longer contains `TSLA` or `META`: neither had listed in the window. All
single-name shocks were removed from the built-in library — supply your own.

If you inverted the sign on short quantities to work around the `abs()` behaviour, remove
that workaround and pass the signed quantity.

## Production Implementation Reference

- Code: `scripts/stress_tester.py` (`HistoricalStressTester`, `CrashScenario`,
  `ScenarioResult`, `StressTestReport`, `BUILTIN_SCENARIOS`).
- Tests: `scripts/test_stress_tester.py`.
- Regulatory scope and scenario provenance: `references/standards.md`.
