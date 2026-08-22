# Workflows for Corporate Action Adjustments

## 1. Event registration

Record each event as `ex_date`, `event_type`, `value`:

| `event_type` | `value` means | Example |
|---|---|---|
| `SPLIT` | share multiplier | `2.0` = 2-for-1; `1.1` = 10% stock dividend |
| `REVERSE_SPLIT` | shares consolidated into one | `5.0` = 1-for-5 |
| `DIVIDEND` | per-share cash amount | `1.50` = $1.50/share |

The date is the **ex-date**, not the declaration, record or pay date. The ex-date is
when the price adjusts; the pay date is when the cash lands. Confusing them shifts the
whole factor by days to weeks. Types are normalised to upper case; anything else raises.

## 2. Single-event factor

- Split, ratio `R`: `alpha = 1 / R`
- Reverse split, ratio `R`: `alpha = R`
- Cash dividend `D`: `alpha = 1 - D / P_ref`, where `P_ref` is the close of the **last
  bar strictly before the ex-date**

Reject rather than coerce: `R <= 0`, non-finite values, `P_ref <= 0`, and `D >= P_ref`
(which would make adjusted prices zero or negative — bad data, or a liquidating
distribution needing an out-of-band factor).

## 3. Cumulative factor

`CAF_t = prod(alpha_E for every event E with ex_date_E > t)`

Two series are built:

- **price CAF** (`caf`) — all event types.
- **share-count CAF** (`volume_caf`) — `SPLIT` and `REVERSE_SPLIT` only.

Both are `1.0` on the most recent bar. Events with an ex-date after the last bar are not
applied; events with an ex-date at or before the first bar multiply nothing. Factors are
keyed by date, so an ex-date on a non-trading day still applies, and a duplicated bar
cannot double-apply one.

## 4. Series transformation

- `P_adj(t) = P_raw(t) * caf_t` — applied to open, high, low and close alike.
- `V_adj(t) = V_raw(t) / volume_caf_t`.

A 2-for-1 split gives `caf = volume_caf = 0.5` before the ex-date: prices halve, volume
doubles, notional turnover is unchanged. A cash dividend gives `caf < 1` and
`volume_caf == 1`: prices scale, volume does not.

## 5. Point-in-time reconstruction

For a walk-forward backtest at simulated date `T`, call `adjust_bars(bars, as_of=T)`.
Bars after `T` and events with an ex-date after `T` are both excluded, so the series is
the one a researcher would have held on `T`. Recomputing per step is O(n) in the bars up
to `T`; for long universes, cache the factor map and extend it forward rather than
rebuilding from the start of history each bar.

## 6. Execution protocol

| Consumer | Series |
|---|---|
| Indicators, signals, returns, correlation, volatility | `adj_*` prices |
| ADV, liquidity and capacity screens | `adj_volume` |
| Order quantity, cash debit/credit, commission, tick rounding, margin | `raw_*` prices |

On each dividend ex-date, credit cash explicitly from the event log and the raw
position: `cash += shares_held * D`. The adjusted price series removed the ex-date drop;
it did not pay anyone. Crediting the dividend *and* treating the adjusted series as a
total-return series double-counts it.

## 7. Reconciliation

Before trusting a run, spot-check the adjusted series against a second source at a known
event. Expect small differences: vendors disagree on whether special dividends are
adjusted at all, on rounding, and on how far back a restatement is applied. A difference
in the third decimal is methodology; a factor-of-two difference is a missed split. See
`vendor-specific-adjustment-methodology-reconciliation`.
