# Standards for Synthetic Labels from the Triple Barrier Method

## Engineering requirements

| Requirement | Standard | Source |
|---|---|---|
| Barrier width unit | The horizontal barriers MUST be sized as multiples of a return target $\sigma_t$ — AFML's `trgt`, "the unit width of the horizontal barriers" — not as fixed percentages. `pt_mult` and `sl_mult` are `ptSl[0]` and `ptSl[1]`. | López de Prado (2018), Snippet 3.2 |
| Barrier test | Touch tests MUST be strict: profit-taking on $r_k > pt \cdot \sigma_t$, stop-loss on $r_k < -sl \cdot \sigma_t$. A price resting exactly on a barrier has not touched it. | López de Prado (2018), Snippet 3.2 (`df0[df0 > pt]`, `df0[df0 < sl]`) |
| Disabled barrier | A multiplier of $0$ MUST disable that barrier rather than place it at the entry price. | López de Prado (2018), Snippet 3.2 (`if ptSl[0] > 0`) |
| First touch wins | The label MUST be decided by the earliest barrier contact in index order, and the exit timestamp MUST be that contact. | López de Prado (2018), Snippet 3.3 (`events['t1'] = df0.min(axis=1)`) |
| Degenerate targets | Events MUST be filtered on $\sigma_t >$ `min_target_return` and dropped when the target is undefined. A zero-width barrier is not a barrier. | López de Prado (2018), Snippet 3.3 (`trgt = trgt[trgt > minRet]`, `dropna(subset=['trgt'])`) |
| Symmetric barriers without a side | When no side is supplied the multipliers SHOULD be equal: "when we cannot differentiate between a profit-taking barrier and a stop-loss barrier due to lack of side knowledge, we must use symmetric horizontal barriers." | López de Prado (2018), Snippet 3.3 (`side is None` → `ptSl_ = [ptSl[0], ptSl[0]]`) |
| Side adjustment | Path returns MUST be multiplied by the side of the bet, so a short's profit barrier lies below the entry price. | López de Prado (2018), Snippet 3.2 (`(df0 / close[loc] - 1) * events_.at[loc, 'side']`) |
| Meta-label | With a side supplied, the binary target MUST be $1$ when the side-adjusted return is positive and $0$ otherwise. | López de Prado (2018), Snippet 3.5/3.7 (`out.loc[out['ret'] <= 0, 'bin'] = 0`) |
| Vertical-barrier label | With no side supplied, an event that reaches the vertical barrier first MUST be labelled $0$, not $\text{sign}(r)$. | López de Prado (2018), Ch. 3 `getBins` (vertical-touch rows set to `0.`) |
| Target causality | $\sigma_t$ MUST be estimable at the close of bar $t$; the forward scan MUST begin at $t+1$. | Repository mandate; `lookahead-bias-elimination` |
| Input integrity | Non-finite prices, non-positive prices, duplicate index labels, non-monotonic indices, unknown event labels and malformed bar ranges MUST raise rather than be coerced or skipped. | Repository mandate |

## Defaults used by this engine

| Parameter | Default | Note |
|---|---|---|
| `pt_mult`, `sl_mult` | $2.0$, $2.0$ | Symmetric, per Snippet 3.3's no-side rule. v1.0.0 shipped $2.0/1.0$ — see the class-balance table. |
| `vertical_bars` | $10$ | Bars, not calendar days. Must match the strategy's real holding period. |
| `volatility_span` | $20$ | A house choice. AFML's `getDailyVol` (Snippet 3.1) defaults to a span of $100$. |
| `volatility_min_periods` | $2$ | The minimum for a standard deviation to exist. Raise it to discard unstable warm-up estimates. |
| `min_target_return` | $0.0$ | AFML's `minRet`, with a strict `>` so zero-volatility events are dropped. |
| `scale_target_by_horizon` | `False` | When `True`, the target becomes $\sigma_t\sqrt{h}$ with $h =$ `vertical_bars`. |

**None of these are standards.** The book prescribes no multiplier, span or horizon; they are dataset-specific choices that must be recorded alongside any label set, because the class balance is a function of all four.

## Measured class balance

Driftless geometric Brownian motion, $\sigma = 1\%$ per bar, 20,000 bars, `vertical_bars=10`, `volatility_span=20`, seed 7, one event per bar. There is no directional signal in this series, so any asymmetry between $+1$ and $-1$ is manufactured by the barriers:

| Configuration | $-1$ | $0$ | $+1$ |
|---|---|---|---|
| `pt=2.0, sl=1.0` (v1.0.0 defaults) | 58.9% | 6.8% | 34.3% |
| `pt=2.0, sl=2.0` (v2.0.0 defaults) | 41.0% | 18.7% | 40.3% |
| `pt=1.0, sl=1.0` | 50.2% | 1.2% | 48.7% |
| `pt=2.0, sl=2.0`, horizon-scaled | 4.2% | 91.3% | 4.5% |
| `pt=0.75, sl=0.75`, horizon-scaled | 36.0% | 28.6% | 35.4% |
| `pt=1.0, sl=1.0`, horizon-scaled | 25.1% | 49.5% | 25.4% |

Two things to read from this. First, the 24-point gap in the top row is an artefact of an unequal stop distance, not a market view — the exact error Snippet 3.3 exists to prevent. Second, a per-bar $\sigma$ over a 10-bar horizon makes the vertical barrier nearly vestigial; the horizon-scaled rows show that once the target is $\sigma\sqrt{h}$, multipliers near $0.75$ rather than $2.0$ are what produce a usable three-class split. Reproduce these figures on your own series before treating any class distribution as informative.

## Verified sources

**López de Prado, M. (2018). *Advances in Financial Machine Learning*. Wiley. Chapter 3, "Labeling."**

- *Snippet 3.1, `getDailyVol(close, span0=100)`* — the volatility target: an exponentially weighted standard deviation of returns, `df0.ewm(span=span0).std()`, computed on arithmetic returns over a one-day lookback. This engine estimates on **log** returns with a default span of 20 and applies the result to arithmetic path returns; the unit mismatch is of order $\sigma^2/2$ (0.005% at $\sigma = 1\%$) and is documented in the module rather than silently absorbed.
- *Snippet 3.2, `applyPtSlOnT1`* — `pt = ptSl[0] * events_['trgt']` when `ptSl[0] > 0`, `sl = -ptSl[1] * events_['trgt']` when `ptSl[1] > 0`; the path is `df0 = (close[loc:t1] / close[loc] - 1) * events_.at[loc, 'side']`; touches are `df0[df0 < sl[loc]].index.min()` and `df0[df0 > pt[loc]].index.min()`. Strict inequalities, side-adjusted path returns, first touch by index order.
- *Snippet 3.3, `getEvents`* — `trgt = trgt[trgt > minRet]`; `if side is None: side_, ptSl_ = pd.Series(1., index=trgt.index), [ptSl[0], ptSl[0]]`; the event's `t1` becomes the earliest of the three barrier times, `df0.dropna(how='all').min(axis=1)`.
- *`getBins`* — `out['ret'] = px.loc[t1] / px.loc[t0] - 1`; `if 'side' in events_: out['ret'] *= events_['side']`; `out['bin'] = np.sign(out['ret'])`, with vertical-barrier touches overwritten to `0.` when no side is present, and `out.loc[out['ret'] <= 0, 'bin'] = 0` — the binary meta-label — when a side is present.

Snippet text above was cross-checked against a reference reproduction of the book's code (<https://github.com/BlackArbsCEO/Adv_Fin_ML_Exercises/blob/master/src/features/snippets.py>) and against the Hudson & Thames `mlfinlab` labeling documentation (<https://random-docs.readthedocs.io/en/latest/implementations/tb_meta_labeling.html>), which states the same barrier-to-label mapping ("upper barrier → 1, lower barrier → -1, vertical barrier → 0") and the same meta-labelling behaviour for `side_prediction`.

## Stated limitations

1. **Bar granularity is the resolution limit.** Close-only scanning cannot see a barrier touched and reversed inside one bar; `highs`/`lows` scanning cannot see the *order* of two touches inside one bar. The engine resolves the latter to the stop-loss and flags `intrabar_ambiguous`. Only a finer bar series or tick data resolves it properly.
2. **The intrabar exit price is a best case.** It is the barrier level, i.e. the fill a resting stop or limit at that level receives in a continuous market. A gap through the barrier fills worse, and the difference is not modelled here — see `execution-realistic-simulation`.
3. **No CUSUM or other event filter is implemented.** AFML seeds barriers on filtered events (op. cit. Snippet 2.4); this engine accepts such an event list via `events` but does not produce one. Seeding every bar, the default, maximises overlap.
4. **Labels overlap and are not IID.** Correcting for that is out of scope and belongs to `sample-weighting-for-overlapping-labels`.
5. **Events without a full horizon are dropped, not truncated.** AFML truncates them (`events_['t1'].fillna(close.index[-1])`). Dropping avoids mixing labels drawn over different horizons into one target, at the cost of losing the last `vertical_bars` events; the count is logged at WARNING.
6. **The volatility estimator is a proxy, not a forecast.** An EWM standard deviation of recent returns is backward-looking by construction. In a regime break the barriers are sized for the regime that just ended — visible as a burst of same-signed labels, not as an error.
