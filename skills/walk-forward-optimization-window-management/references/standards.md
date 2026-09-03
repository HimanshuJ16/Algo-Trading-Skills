# Broker & Framework Coverage — walk-forward-optimization-window-management

| Window Mode | In-Sample Behavior | Out-of-Sample Behavior | Recommended Use Case |
|---|---|---|---|
| Rolling WFO | Fixed-length sliding window (e.g. 1 year) | Fixed-length testing window (e.g. 3 months) | Fast-regime changing markets (Crypto, Forex) |
| Anchored WFO | Expanding window anchored to start date | Fixed-length testing window (e.g. 3 months) | Structural long-term equities / macro trend |

## Category

`backtesting-methodology` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Window Geometry Contract

All bounds are inclusive calendar days. One slice occupies:

```
[warmup_start .. is_start-1]   indicator warm-up  — loaded for state only, never scored
[is_start     .. is_end     ]  in-sample          — parameter optimization
[is_end+1     .. embargo_end]  purge/embargo gap  — never trained on, never scored
[oos_start    .. oos_end    ]  out-of-sample      — the only interval that is scored
```

Consequences the implementation enforces rather than assumes:

- `min_required_days() == in_sample_days + embargo_days + out_of_sample_days`, counted
  inclusively. A dataset spanning exactly that many days yields exactly one slice.
- `step_days >= out_of_sample_days`, otherwise consecutive OOS intervals overlap and stitching
  them double-counts days. Overriding this requires `allow_overlapping_oos=True`, and the
  resulting slices must not be concatenated.
- `step_days > out_of_sample_days` is legal but leaves untested calendar gaps that are absent
  from the stitched OOS curve; `validate_slice_sequence()` logs each gap.
- `step_days <= 0` is rejected at construction. It cannot advance the cursor and previously
  generated slices without terminating.

## Purge / Embargo

Strict chronological ordering (`is_end < oos_start`) is necessary but **not sufficient** for a
leak-free split. Two channels cross an adjacent boundary:

- **Feature lookback.** A feature with an `L`-bar lookback evaluated on the first `L` OOS bars
  is computed partly from in-sample bars.
- **Label horizon.** A label with an `H`-bar forward horizon assigned to the last `H` in-sample
  bars is realised from out-of-sample outcomes.

The remedy is to purge/embargo a gap of at least `max(L, H)` between the two intervals, which is
what `embargo_days` sets and what `validate_window_isolation(slice, min_embargo_days)` checks.
Purging (removing training observations whose labels overlap the test set) and embargoing
(excluding observations adjacent to the test set) are defined in Marcos López de Prado,
*Advances in Financial Machine Learning* (Wiley, 2018), ch. 7 "Cross-Validation in Finance",
§7.4.1 *Purging the Training Set* and §7.4.2 *Embargo*.

The default `embargo_days=0` preserves the historical behaviour of this helper and emits a
warning; it is only correct for a strategy with no feature lookback and no multi-bar label.

## Walk-Forward Efficiency

The classical WFE of walk-forward analysis is the ratio of the **annualized rate of return**
out-of-sample to the annualized rate of return in-sample, with a value of 50% or more treated as
a successful walk-forward — see the [TradeStation Walk-Forward Optimizer documentation](https://help.tradestation.com/09_01/tswfo/topics/walk-forward_summary_out-of-sample.htm),
which follows the walk-forward analysis methodology of Robert Pardo, *The Evaluation and
Optimization of Trading Strategies* (Wiley, 2nd ed., 2008).

`calculate_wfe()` applies the same ratio convention to the **Sharpe ratio**, which is the
variant this skill standardises on. That is a deliberate substitution, not the classical
definition; report which quantity a published WFE figure was computed on.

The 0.50 threshold is a practitioner heuristic. It is not a regulatory requirement, not a
significance test, and carries no guarantee about live performance.

## Safety Invariants

- `in_sample_days >= 1`, `out_of_sample_days >= 1`, `step_days >= 1`; `warmup_days >= 0`,
  `embargo_days >= 0`. All are integers (`bool` is rejected).
- `start_date` and `end_date` are plain `datetime.date` values with `end_date >= start_date`.
  A `datetime.datetime` is rejected rather than truncated, because it is a `date` subclass whose
  arithmetic silently yields time-bearing bounds.
- `min_wfe_threshold` and `min_is_sharpe` must be finite.
- WFE is defined only when `is_sharpe > min_is_sharpe` and both Sharpe inputs are finite.
  Otherwise `wfe_ratio` is NaN, `is_robust` is False, and `undefined_reason` is populated. The
  denominator is never clamped: flooring a negative in-sample Sharpe at an epsilon converts a
  losing in-sample fit into a spuriously large "robust" ratio.
- Both Sharpe inputs must share an annualization basis; the helper takes scalars and cannot
  verify this, so it is the caller's obligation.

## Regulatory & Operational Notes

This skill is a research-methodology control, not a regulated control. It has no direct
regulatory mandate. It supports, but does not satisfy, the model-validation and testing
expectations that apply to algorithmic trading systems — for example the pre-deployment testing
and annual review duties of [EU MiFID II RTS 6](https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng)
(Commission Delegated Regulation (EU) 2017/589) and the development, testing and supervision
practices in [FINRA Regulatory Notice 15-09](https://www.finra.org/rules-guidance/notices/15-09).
Whether either applies depends on jurisdiction, firm registration status, and instrument.

It also intersects with backtest-overfitting measurement — notably the Probability of Backtest
Overfitting (PBO) framework of Bailey, Borwein, López de Prado and Zhu, ["The Probability of
Backtest Overfitting"](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253) — which
quantifies selection bias across the parameter trials that WFE does not account for. Pair this
skill with `walk-forward-hyperparameter-search-budget` for that dimension.
