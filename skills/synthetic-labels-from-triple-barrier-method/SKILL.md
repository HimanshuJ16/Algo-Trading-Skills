---
name: synthetic-labels-from-triple-barrier-method
description: Use when building the supervised target for a financial ML classifier,
  to label each event by which of three barriers its forward price path touches first
  — a volatility-scaled profit-taking barrier (+1), a volatility-scaled stop-loss
  barrier (-1), or a vertical time-out (0) — instead of a naive fixed-horizon return,
  and to derive López de Prado meta-labels when a primary model supplies the side
domain: algorithmic-trading
subdomain: financial-ml
tags:
- financial-ml
- triple-barrier-method
- lopez-de-prado
- meta-labeling
- path-dependent-labels
- volatility-scaled-barriers
brokers_frameworks:
- Triple Barrier Labeler Engine
- pandas
- NumPy
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this when the target for a supervised model is "what happened next" and the *path* matters. A fixed-horizon label — the sign of the 10-bar forward return — treats a position that fell 8% on day two and clawed back to +0.2% by day ten as a winner. No account trading that position would have survived it. The Triple-Barrier Method (López de Prado, *Advances in Financial Machine Learning*, Wiley 2018, Ch. 3) labels the event by whichever of three barriers the path touches **first**:

- an upper profit-taking barrier at path return $+pt \cdot \sigma_t$ → $+1$;
- a lower stop-loss barrier at path return $-sl \cdot \sigma_t$ → $-1$;
- a vertical barrier `vertical_bars` bars later → $0$.

Both horizontal barriers are scaled by $\sigma_t$, the per-bar volatility estimated at the entry bar, so a 2% move counts as a target in a quiet regime and as noise in a violent one — the same fixed 2% target would be unreachable in the first and hit by lunchtime in the second.

Supply `side` when a primary model has already decided direction: path returns are multiplied by the side, the profit barrier for a short sits *below* the entry, and the output carries the AFML `getBins` binary meta-label (take the bet / pass) alongside the ternary barrier code.

## When NOT to Use

- **As a trading rule.** These are labels, not signals. The barriers are placed with $\sigma_t$ known at the entry bar, but the *outcome* is read from bars that have not happened yet at decision time. Training on them is correct; routing on them is a time machine.
- **On a series you have not cleaned.** The engine rejects NaN, infinite, non-positive prices, duplicate index labels and non-monotonic indices rather than labelling around them, because each of those silently produced a wrong label in v1.0.0 rather than an error. Fix the data upstream — see `point-in-time-database-for-ml-training-data`.
- **Without weighting the overlap afterwards.** Seeding an event on every bar with a `vertical_bars` horizon makes each label share up to `vertical_bars - 1` bars of path with its neighbours. The labels are not IID, and a model trained on them as if they were reports memorisation as accuracy. `sample-weighting-for-overlapping-labels` computes the uniqueness weights; this skill's `entry_timestamp`/`exit_timestamp` columns are its input.
- **With asymmetric multipliers and no side.** AFML Snippet 3.3 forces `ptSl_ = [ptSl[0], ptSl[0]]` when the side is unknown, because without a direction there is nothing to distinguish a profit barrier from a stop. The engine allows asymmetry but logs a WARNING — see the class-balance figures in `references/standards.md`.
- **On close prices when the strategy has a real stop.** A close-only scan cannot see a stop breached mid-bar and recovered by the close; it labels that event $+1$ or $0$ when the account would have been stopped out. Pass `highs`/`lows`.
- **On daily bars to label an intraday strategy, or vice versa.** $\sigma_t$ is per-bar and the horizon is in bars. The two must describe the same clock as the strategy they will be traded on.

## Prerequisites

- A close-price `pandas.Series`, strictly positive and finite, on a **unique, monotonically increasing** index at one consistent bar frequency.
- Enough bars *before* the first event to warm up the volatility estimator. The EWM standard deviation is undefined until `volatility_min_periods` returns exist; those events are dropped, not defaulted.
- Barrier multipliers you can defend, and a `vertical_bars` horizon that matches the holding period the strategy will actually run. The defaults (`pt_mult=2.0`, `sl_mult=2.0`, `vertical_bars=10`, `volatility_span=20`) are house choices, not standards — AFML's own `getDailyVol` uses a span of 100.
- Optional `highs`/`lows` aligned bar-for-bar with the closes, if intrabar barrier detection is wanted.
- Optional `side` (+1/-1), from a primary model, for meta-labelling.

## Workflow

1. **Estimate the volatility target $\sigma_t$**:
   - $\sigma_t = \text{EWMStd}_{\text{span}}\!\left(\log \frac{P_t}{P_{t-1}}\right)$, the role AFML's `trgt` plays: "the unit width of the horizontal barriers" (Snippet 3.2).
   - **Decision point — the estimate must end at the entry bar, not after it.** Its last input is $\log(P_t/P_{t-1})$, known at the close of bar $t$, which is also the entry price; the forward scan starts at $t+1$. Nothing about a label's own future sizes its barriers.
   - **Decision point — a warm-up bar has no target, and no target is not a small target.** v1.0.0 ran `.fillna(0.01)`, handing the first bars a fabricated 1% barrier width that no caller could see. Those events are now dropped with a WARNING naming the count.

2. **Filter events that cannot carry a barrier**:
   - Keep an event only while $\sigma_t >$ `min_target_return`, mirroring `getEvents`' `trgt = trgt[trgt > minRet]`.
   - **Decision point — a zero-volatility event is not a flat market, it is a broken one.** A halted, illiquid or forward-filled instrument gives $\sigma_t = 0$, collapsing both barriers onto the entry price. In v1.0.0 the non-strict `>=` then read every unchanged price as a profit-take: a constant 30-bar series produced 24 `+1` labels, each with a realised return of exactly 0.00. Such events are now dropped.
   - Events within `vertical_bars` of the end of the series are dropped rather than labelled over a truncated horizon.

3. **Size the barriers and scan forward**:
   - Path return at bar $k$: $r_k = (P_k/P_t - 1) \cdot \text{side}$. Take-profit if $r_k > pt \cdot \sigma_t$; stop-loss if $r_k < -sl \cdot \sigma_t$; otherwise continue to $k+1$.
   - **Decision point — the inequalities are strict**, as in Snippet 3.2's `df0[df0 > pt]` / `df0[df0 < sl]`. A price resting exactly on the barrier has not touched it. This is what makes a zero-width barrier inert rather than universally triggered.
   - **Decision point — a multiplier of `0` disables that barrier** (AFML: `if ptSl[0] > 0`), turning the method into a stop-only or target-only label. Both cannot be zero.
   - **Decision point — with `highs`/`lows`, a bar that spans both barriers resolves to the stop-loss.** The intrabar path is unknown; assuming the favourable ordering is how a backtest turns a stop-out into a winner. The row is flagged `intrabar_ambiguous` so those labels can be counted, excluded, or re-derived from finer bars.
   - In intrabar mode the exit price is the **barrier level**, the fill a resting stop or limit at that level would get. A gap straight through the barrier fills worse than that; treat the intrabar exit price as an upper bound on realised quality, not a promise.

4. **Emit the label and hand it on**:
   - Each row carries entry/exit timestamps and prices, the barrier code, the side-adjusted `realized_return`, the `target_volatility` used, `holding_bars`, and — when `side` was supplied — the `meta_label` (1 if the bet made money, else 0, per `getBins`).
   - **Decision point — no event surviving is an error, not an empty answer.** The call raises rather than returning an empty frame, which a training pipeline would otherwise consume as "no signal".
   - Feed `entry_timestamp`/`exit_timestamp` to `sample-weighting-for-overlapping-labels` before fitting, and inspect the class balance before believing it (step 1 of the checklist).

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Reading a barrier-induced class skew as a market view.** On a driftless random walk (20,000 bars, $\sigma = 1\%$/bar, `vertical_bars=10`), v1.0.0's asymmetric defaults `pt=2.0, sl=1.0` produced 58.9% $-1$ against 34.3% $+1$. Nothing in that series is bearish; the stop was simply half as far away. Symmetric multipliers on the same series give 41.0% / 40.3%.
- **A vertical barrier that never fires.** $\sigma_t$ is a *per-bar* width but the horizon is `vertical_bars` long, and a random walk covers roughly $\sigma\sqrt{h}$ over $h$ bars. At the defaults only 18.7% of labels time out; at `pt=sl=1.0` it is 1.2%, and the "third class" is decorative. Either widen the multipliers or set `scale_target_by_horizon=True` — measured splits are in `references/standards.md`.
- **Labelling on closes when the strategy has a stop.** The close-only scan is blind to a stop breached and recovered within one bar. Those events are labelled $+1$ or $0$, teaching the model that positions it would never have held were profitable.
- **A NaN close treated as an untouched barrier.** `NaN >= upper` and `NaN <= lower` are both `False`, so v1.0.0's scan stepped straight past an unusable bar — and a NaN *entry* price emitted a label with a NaN return. Non-finite prices are now rejected on input.
- **Silently receiving fewer labels than events.** v1.0.0 resolved event timestamps with `list.index()` and dropped anything it could not find, so a CUSUM filter's 400 events could return 260 rows with no warning. Unknown labels now raise; horizon-truncated ones are dropped with a logged count.
- **An unsorted or duplicated index.** Barrier scanning is positional. A series whose timestamps are out of order labels each event against the wrong future and still returns a plausible-looking frame; both are now rejected.
- **Applying long labels to a short book.** Without `side`, every label describes a long position, and the asymmetric multipliers land on the wrong barriers when the bet is a short. Pass `side` and read `meta_label`.
- **Treating the vertical-barrier label as "no move".** A $0$ means neither barrier was touched within the horizon, not that the return was zero — the timeout return can be sizeable and is reported in `realized_return`.
- **Training on overlapping labels as if they were IID.** With `vertical_bars=10` and an event on every bar, each label shares nine bars of path with its neighbour. See `sample-weighting-for-overlapping-labels`.
- **Comparing label sets built with different parameters.** The class balance is a function of `pt_mult`, `sl_mult`, `vertical_bars` and `volatility_span`. Record them with the dataset or the labels are not reproducible.

## Verification

- **Volatility target.** `compute_volatility` must match an independently written adjusted-EWM unbiased weighted standard deviation of log returns — weight $(1-\alpha)^{t-j}$, $\alpha = 2/(\text{span}+1)$ — to 12 decimal places, and must leave warm-up bars `NaN` rather than filling them.
- **Causality.** Appending bars to the end of the series must not change any earlier $\sigma_t$.
- **Barrier identity.** An upward spike labels $+1$ on the first close above the barrier, a crash labels $-1$, a flat drift times out at exactly `vertical_bars` holding bars, and each `realized_return` must equal the return recomputed from the raw prices.
- **Boundary (regression).** Place a close exactly on $P_t(1 + pt\,\sigma_t)$: it must **not** touch. Multiply it by $1 + 10^{-9}$: it must. A constant 30-bar series must raise, where v1.0.0 returned 24 `+1` labels with zero return.
- **Side symmetry.** The same crash must label $-1$ for `side=+1` and $+1$ for `side=-1`, with the same exit timestamp, a sign-flipped `realized_return`, and `meta_label` 1 for the short and 0 for the long.
- **Intrabar.** A bar whose low breaches the stop but whose close recovers must label $0$ close-only and $-1$ with `highs`/`lows`, exiting at exactly $P_t(1 - sl\,\sigma_t)$. A bar spanning both barriers must label $-1$ with `intrabar_ambiguous` set.
- **Negative checks.** NaN/infinite/non-positive prices, duplicate or non-monotonic index, a series shorter than the horizon, a non-Series input, an unknown/duplicate/empty `events` list, a misaligned or invalid `side`, highs below lows, a close outside its own bar's range, and every out-of-range constructor argument must each raise `TripleBarrierError` (a `ValueError` subclass).
- Run `python -m unittest discover -s skills/synthetic-labels-from-triple-barrier-method/scripts` and confirm a 100% pass rate.

## Related Skills

- `sample-weighting-for-overlapping-labels`
- `label-noise-estimation-in-financial-targets`
- `class-imbalance-handling-for-rare-signal-events`
- `feature-engineering-without-leakage`
- `lookahead-bias-elimination`
- `walk-forward-validation-setup`
- `hyperparameter-tuning-without-target-leakage`
- `point-in-time-database-for-ml-training-data`
