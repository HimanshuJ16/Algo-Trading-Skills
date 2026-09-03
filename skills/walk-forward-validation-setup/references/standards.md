# Broker & Framework Coverage — walk-forward-validation-setup

| Tool | Native time-series splitting | What it does not give you |
|---|---|---|
| [scikit-learn `TimeSeriesSplit`](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html) | Expanding (anchored) folds. `gap` — "number of samples to exclude from the end of each train set before the test set" — and `test_size` were both added in **0.24**; `max_train_size` caps the training length. | No explicit rolling mode (`max_train_size` caps rather than declares one), no check of the gap against a feature lookback or label horizon, no per-fold or cross-fold out-of-sample metric aggregation. |
| [QuantConnect LEAN](https://www.quantconnect.com/docs/v2/writing-algorithms/optimization/walk-forward-optimization) | Documents a walk-forward optimization workflow built on the platform's parameter optimizer and scheduled re-optimization. | It is an optimization workflow for LEAN algorithms, not a fold generator for an offline feature matrix. |
| [Backtrader](https://community.backtrader.com/topic/142/how-to-implement-walk-forward-optimization-with-backtrader) | None. Walk-forward is implemented manually in user code; the community threads above are the reference examples. | Everything in this skill. |
| `scripts/walk_forward.py` | Row-index folds, EXPANDING and ROLLING, explicit purge/embargo gap, gap checked against `max(L, H)`, per-fold and cross-fold aggregation. | Not a backtester, not an execution model, and no per-observation purging for overlapping labels. |

## Category

`backtesting-methodology` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Purge and Embargo

Strict chronological ordering (`train_end < test_start`) is necessary but **not sufficient**.
Two channels cross an adjacent boundary:

- **Label horizon `H`.** A label with an `H`-bar forward horizon attached to the last `H`
  training rows is realised from out-of-sample bars — the training set literally contains the
  answer. Removing those rows is *purging*.
- **Feature lookback `L`.** A feature with an `L`-bar backward window evaluated on the first
  `L` test rows is computed partly from training bars.

A gap of `max(L, H)` covers both, and `generate_splits(max_feature_lookback=..., label_horizon=...)`
enforces it. Purging (removing training observations whose label formation window overlaps the
test set) and embargoing (excluding observations adjacent to a test fold, to absorb serial
correlation) are defined in Marcos López de Prado, *Advances in Financial Machine Learning*
(Wiley, 2018), ch. 7 "Cross-Validation in Finance", §7.4.1 *Purging the Training Set* and §7.4.2
*Embargo*.

`DEFAULT_EMBARGO = 20` is a starting point, not a safe default. Calling `generate_splits()`
without `max_feature_lookback` or `label_horizon` logs a warning that the gap is unverified;
supplying either turns the check into a hard error when the gap is too small.

Note the asymmetry of this layout. Because training always precedes testing within a fold, the
gap acts as a purge. The embargo proper — the rows *after* a test window — matters across folds:
under both EXPANDING and ROLLING, the rows immediately following fold *k*'s test window are
absorbed into fold *k+1*'s training window. That does not corrupt fold *k*'s already-computed
result, but it does correlate consecutive folds, so per-fold results are not independent draws
and should not be treated as such in a significance test.

## Metric Conventions

Conventions match `backtest-reporting-standardized-tearsheet` so figures are comparable across
the library:

- **Sharpe** = (arithmetic annualized excess return) / (annualized sample volatility)
  = `(mean(r) * periods_per_year - risk_free_rate) / (std(r, ddof=1) * sqrt(periods_per_year))`.
  Undefined — reported as `None`, never clamped to an epsilon — below two observations or on a
  constant return series.
- **`max_drawdown_pct`** is a **non-positive** fraction: `-0.05` is a 5% drawdown. The equity
  curve is seeded at 1.0 so a decline beginning on the first test period is measured from
  starting capital.
- **`win_rate`** is a per-period hit rate (`fraction of test periods with return > 0`), **not** a
  per-trade win rate and **not** classification accuracy. A flat period counts in the denominator
  and is not a win.
- All three are `None` unless a `returns_fn` supplies realised strategy returns. Mapping a
  prediction to a position is strategy-specific and the harness does not assume one.

## Safety Invariants

- `train_size >= 1`, `test_size >= 1`, `embargo_size >= 0`, `n_rows >= 0`, `periods_per_year >= 1`;
  all integers, with `bool` rejected rather than coerced. `test_size >= 1` is a termination
  condition: the fold cursor advances by `test_size`, so `0` would generate identical folds
  forever.
- `mode` must be a `SplitMode`; the string wrapper `walk_forward_splits()` accepts only
  `"expanding"` / `"rolling"` (case-insensitive) and raises on anything else, rather than
  treating an unrecognised value as rolling.
- With `timestamp_col` supplied, the frame must be sorted non-decreasing; ties are allowed,
  missing timestamps are not.
- `fit_predict_fn` must return one prediction per test row. A mismatched length raises instead of
  broadcasting into a meaningless accuracy.
- `returns_fn` output must be finite, one value per test row, and no worse than `-1.0`.
- `target_col` is dropped from the frame passed to `fit_predict_fn` unless
  `hide_test_labels=False` is set explicitly.

## Regulatory & Operational Notes

This skill is a research-methodology control, not a regulated control. It carries no direct
regulatory mandate. It supports, but does not by itself satisfy, the pre-deployment testing and
model-validation expectations that apply to algorithmic trading systems — for example the
testing and annual self-assessment duties of [EU MiFID II RTS 6](https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng)
(Commission Delegated Regulation (EU) 2017/589), and [FINRA Regulatory Notice 15-09](https://www.finra.org/rules-guidance/notices/15-09),
*Guidance on Effective Supervision and Control Practices for Firms Engaging in Algorithmic
Trading Strategies*, which states that "testing of algorithmic strategies prior to being put into
production is an essential component of effective policies and procedures". Whether either
applies depends on jurisdiction, firm registration status, and instrument.

No threshold in this skill — fold count, embargo length, Sharpe level — is a regulatory
requirement. The "at least 3-5 folds" guidance is a practitioner heuristic for making regime
dependence visible, not a statistical test and not a compliance standard.
