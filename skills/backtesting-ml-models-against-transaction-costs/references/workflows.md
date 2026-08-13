# ML Backtesting TCA Workflow

## 1. Extract predictions

Extract the raw continuous predictions (probabilities, expected returns, or Z-scores)
from your ML pipeline. Note the units: `signal_threshold` is compared directly against
these values, so a threshold derived from a cost in basis points only makes sense if
the predictions are decimal returns. For Z-scores or probabilities the threshold is a
conviction cut-off and the cost hurdle must be checked separately.

## 2. Align to forward returns

Align each prediction with the return realised *after* it was observable, accounting
for execution delay. If the model consumes the bar-`t` close and you can only trade
the bar-`t+1` open, the return that belongs to `predictions[t]` runs from the `t+1`
open onward — not from the `t` close. See `lookahead-bias-elimination`.

## 3. Clean the series

Remove NaN/Inf from both arrays before calling the backtester. `MlTcaBacktester`
raises `ValueError` on non-finite input by design: a NaN prediction silently mapped to
"flat" changes the strategy being measured, and a NaN return propagates into every
compounded metric.

## 4. Configure

Instantiate `MlTcaBacktester(MlTcaBacktesterConfig(...))`:

| Field | Meaning |
|---|---|
| `bps_cost_half_turn` | Basis points per unit of turnover (slippage + fees). |
| `signal_threshold` | Minimum \|prediction\| to open a position. Must be > 0. |
| `exit_threshold` | Optional buy/hold spread; hold until \|prediction\| falls below this. `None` = close as soon as the entry threshold is lost. |
| `liquidate_at_end` | Charge the exit half-turn on a position still open at the end (default `True`). |

Invalid configurations (negative cost, non-positive entry threshold, an exit threshold
wider than the entry threshold) raise `ValueError` at construction time.

## 5. Sweep the hurdle out-of-sample

Sweep `signal_threshold` — and, if the model churns, `exit_threshold` — to find where
the model only trades when expected profit exceeds execution cost. Start the sweep at
`2 * bps_cost_half_turn / 10_000`, the round-trip breakeven. Fit the sweep on training
folds and confirm on held-out data; a threshold chosen to maximise net return on the
reported sample is a mined parameter. See
`walk-forward-optimization-window-management`.

## 6. Analyse the output

`process()` returns:

| Key | Meaning |
|---|---|
| `Total Gross Return` | Compounded return before costs. |
| `Total Net Return` | Compounded return after costs. `-1.0` signals capital was fully eroded within the sample. |
| `Total Turnover (Units)` | Sum of absolute position changes, including terminal liquidation. |
| `Total Trade Count` | Number of periods in which the position changed. |
| `Total Cost Paid` | Arithmetic sum of per-period costs (decimal). Equals turnover × cost rate. |
| `Cost Drag (%)` | `(Total Gross Return − Total Net Return) × 100`, i.e. the gap between the two *compounded* curves in percentage points — not the same number as `Total Cost Paid`. |
| `Net Returns Series`, `Gross Returns Series`, `Positions Series` | Per-period detail for auditing. |

To annualise, divide `Total Turnover (Units)` by the sample length in years, and
convert `Total Net Return` to CAGR yourself — the backtester is calendar-agnostic and
deliberately does not guess a periods-per-year constant.

## 7. Stress the cost assumption

Re-run at 2–3× the estimated `bps_cost_half_turn`. Costs are the least certain input
in the whole exercise (see `references/standards.md`), and a net edge that survives
only at the point estimate is not an edge.
