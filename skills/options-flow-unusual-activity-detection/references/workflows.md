# Workflows — options-flow-unusual-activity-detection

## 1. Validate the print

- `volume` is a positive integer contract count for **this print** (not cumulative
  session volume for the series).
- `execution_price`, `adv` finite and non-negative; `contract_multiplier` finite and
  strictly positive; `option_type` in {CALL, C, PUT, P}.
- `bid`/`ask` finite or `None`.
- **`None` is not `0`.** Unsupplied `open_interest`/`adv` must be `None` — the gate is
  then recorded as unevaluable and cannot clear. `0` is a genuine zero (newly listed or
  never-traded series) and yields an infinite ratio, which does clear.
- Anything else raises `ValueError`. In batch mode `scan()` logs the offending
  `trade_id` and continues, so one malformed message does not blind the scanner.

## 2. Compute the metrics

| Metric | Formula | Notes |
|---|---|---|
| $V/OI$ | print volume / series open interest | OI is the prior session's OCC figure and does not move intraday. |
| $V/ADV$ | print volume / series ADV | ADV of the **series**, never the underlying. |
| Premium | volume × price × contract multiplier | Multiplier is a per-series term; 100 is the standard US listed default, not a universal constant. |

A zero denominator gives $\infty$, never the raw volume — a contract count is not a
ratio.

## 3. Infer the aggressor side (quote rule)

| Condition | Label |
|---|---|
| price $\ge$ ask | `BUY_AT_ASK` |
| price $\le$ bid | `SELL_AT_BID` |
| bid < price < ask | `MID_MARKET` (not classifiable) |
| quote missing, ask $\le 0$, or bid > ask | `UNCLASSIFIED` |

Use the quote **in force at the print**. Never default a missing quote to a buy: a
zero-filled quote satisfies `price >= ask` for every print and turns a data outage into
a wave of "aggressive bullish sweeps". Accuracy of the rule: 83% of classifiable option
trades (Savickas & Wilson 2003) — see `standards.md`.

## 4. Classify

Flag only when **all three** size gates clear. Then:

| Aggressor | CALL | PUT |
|---|---|---|
| `BUY_AT_ASK` | `UNUSUAL_BULLISH_SWEEP` | `UNUSUAL_BEARISH_SWEEP` |
| `SELL_AT_BID` | `UNUSUAL_BEARISH_BLOCK` | `UNUSUAL_BULLISH_BLOCK` |
| `MID_MARKET` | `UNUSUAL_FLOW_NEUTRAL` | `UNUSUAL_FLOW_NEUTRAL` |
| `UNCLASSIFIED` | `UNUSUAL_FLOW_UNCLASSIFIED` | `UNUSUAL_FLOW_UNCLASSIFIED` |

Any gate not cleared (or unevaluable) $\Rightarrow$ `ROUTINE_FLOW`.

`UNUSUAL_*_BLOCK` labels assume the print **opened** a position. The feed cannot
confirm that; a call sold at the bid may be a long being closed.

## 5. Emit and consume the audit report

`OptionsFlowAnomalyReport` carries the three metrics, `gates_passed`,
`gates_unevaluable`, `aggressor_side`, `classification`, `is_unusual`,
`direction_is_inferred` and a formatted `audit_notes` line. Flagged prints log at
WARNING, routine prints at INFO.

Downstream:

- Filter on `direction_is_inferred` before aggregating sentiment. `MID_MARKET` and
  `UNCLASSIFIED` prints are real size with no usable direction — dropping them is
  correct; counting them as neutral evidence is not.
- Check `gates_unevaluable` before reading an absence of flags as an absence of unusual
  activity. If reference data was missing, nothing could have been flagged.
