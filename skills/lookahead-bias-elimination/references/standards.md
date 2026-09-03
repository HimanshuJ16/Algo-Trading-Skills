# Standards & Framework Coverage — lookahead-bias-elimination

| Framework / Library | Relevance to this skill |
|---|---|
| Python `pandas` | `shift()` sign convention, `rolling(center=...)` and its right-closed default, `ewm()` warm-up defaults, `groupby().shift()` for panel-safe alignment. |
| Python `numpy` | Relative-tolerance price comparison (`isclose(..., rtol=..., atol=0.0)`) so one constant serves both crypto and FX price scales. |
| backtrader | Reference implementation of the next-bar-open execution convention this skill enforces, plus the explicit opt-out (`cheat_on_open` / `broker.set_coc`). |
| backtesting.py | Same convention, exposed as the `trade_on_close` flag. |
| Vectorised pandas backtests (hand-rolled, VectorBT-style) | Where the defect actually originates: an entire frame computed in one pass, with no engine enforcing the bar boundary. |

No third-party dependency beyond `pandas` and `numpy` is required.

## Method Sources

### Execution timing convention

- **backtrader documentation**, [*Cerebro — Cheat-On-Open*](https://www.backtrader.com/docu/cerebro/cheat-on-open/cheat-on-open/). An order issued during `next` is matched against "the next incoming price which is the `open` price" of the following bar. Cheat-on-open is opt-in (`Cerebro(cheat_on_open=True)`, `broker.set_coo`), as is cheat-on-close (`broker.set_coc`). *This is the default the auditor's rule is drawn from; the numeric default of the `coc` parameter is not stated in that page and is not asserted here.*
- **backtesting.py documentation**, [`Backtest`](https://kernc.github.io/backtesting.py/doc/backtesting/backtesting.html): `trade_on_close` — "If `trade_on_close` is `True`, market orders will be filled with respect to the current bar's closing price instead of the next bar's open" — **default `False`**. `Strategy.buy()` states that unless `trade_on_close=True`, market orders are filled on the next bar's open.

Two independent engines defaulting to next-bar-open, and exposing same-bar-close only as a named
opt-in, is why a hand-rolled backtest that fills at the same bar's close should be treated as a
defect rather than a modelling choice.

### pandas window and warm-up semantics

- **pandas documentation**, [`DataFrame.rolling`](https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.rolling.html):
  - `center` — "If `False`, set the window labels as the right edge of the window index. If `True`, set the window labels as the center of the window index." **Default `False`**, so a centred window is always a deliberate argument.
  - `closed` — default `None`, behaving as `'right'`: "uses the window (first, last] meaning the last point is included in the calculations." A trailing window therefore **includes bar T itself**.
  - `min_periods` — "For a window that is specified by an integer, `min_periods` will default to the size of the window," so `rolling(20)` is NaN for the first 19 bars and warms up correctly unaided.
- **pandas documentation**, [`DataFrame.ewm`](https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.ewm.html): `min_periods` **default `0`**, `adjust` default `True`. `ewm(span=20)` therefore emits a value on bar 0 computed from a single observation — the opposite of `rolling`'s behaviour, and the reason warm-up must be audited from the data rather than assumed.

## Audit Limitations (read before signing off)

The screens in `scripts/leak_audit.py` are candidate filters. A report with zero findings means no
screen fired — not that the backtest is causal. Specifically:

1. **The same-bar screen compares values, not provenance.** It matches a fill against Close, High and Low of the same row. A fill drawn from a mid-price, a VWAP, or a bar not in the frame matches nothing and passes.
2. **It also produces false positives.** Where bar T+1 opens exactly at bar T's close — continuous futures, 24h crypto, synthetically stitched series — a correctly aligned fill is numerically identical to a same-bar one and is reported. Verify against the alignment code before changing anything.
3. **The indicator fingerprint recognises exactly two constructions** — `rolling(w, center=True).<agg>()` for a fixed aggregation list, and `price.shift(-k)` — against a single price column. EWMAs, multi-column indicators, and resampled series fit neither and return nothing.
4. **Restated raw history passes everything.** Adjusted closes and backfilled fundamentals are causal *within* the delivered frame. Only the publication timestamp reveals them.
5. **Row order is assumed unless a timestamp column is supplied.** Positions and shifts are computed by row; a frame reordered and then re-indexed is indistinguishable from a sorted one.
6. **Dataset-level tuning leakage is out of scope** — see `walk-forward-validation-setup` and `hyperparameter-tuning-without-target-leakage`.

`run_timing_calibration()` exists because of points 1 and 3: it injects a known violation into every
active signal and reports the fraction detected. Treat any result below 1.0 as a statement that
clean audits on that frame are not trustworthy.

## Regulatory & Operational Notes

**No securities regulator prescribes a lookahead-bias test for a backtest.** Eliminating lookahead
bias is a research-integrity and model-risk control. It becomes a regulatory concern only at the
point where backtested results are *shown to someone else*, and the rules below govern that
presentation rather than the methodology. Jurisdiction and applicability differ sharply, so do not
generalise any of them.

- **United States — SEC-registered investment advisers.** The Marketing Rule, **17 CFR § 275.206(4)-1**, defines *hypothetical performance* at **(e)(8)** as "performance results that were not actually achieved by any portfolio of the investment adviser," and **(e)(8)(i)(B)** brings backtests inside it: "Performance that is backtested by the application of a strategy to data from prior time periods when the strategy was not actually used during those time periods." Paragraph **(d)(6)** then prohibits including hypothetical performance in an advertisement unless the adviser adopts policies and procedures reasonably designed to ensure its relevance to the intended audience, and provides sufficient information for that audience to understand "the criteria used and assumptions made" and "the risks and limitations." A backtest containing lookahead bias cannot satisfy (d)(6)(ii)–(iii), because its stated assumptions are not the ones actually applied. *Applicability: SEC-registered investment advisers, in advertisements as defined at (e)(1) — note that a communication containing hypothetical performance counts as an advertisement even when sent to a single person. It does not bind a proprietary trader or an individual running a backtest for their own account.*
- **Global — GIPS Standards (voluntary).** The **2020 GIPS Standards for Firms** glossary defines *theoretical performance* as "Performance that is not derived from a portfolio or composite with actual assets invested in the strategy presented," explicitly including "model, backtested, hypothetical, simulated, indicative, ex ante, and forward-looking performance." Provision **1.A.27** states: "The firm must not link actual performance to historical theoretical performance." Provision **4.C.48** requires a firm presenting theoretical performance in a GIPS Composite Report to disclose that the results are theoretical and not based on actual assets, to disclose the methodology and assumptions "sufficient for the prospective client or prospective investor to interpret the theoretical performance," to disclose the fee treatment, and to "clearly label the theoretical performance as supplemental information." *Applicability: voluntary. GIPS binds only a firm that elects to claim compliance; it is not law in any jurisdiction.*
- **India — brokers offering algo services.** SEBI Circular **SEBI/HO/MIRSD/DOP/P/CIR/2022/117** (2 September 2022), *Performance/return claimed by unregulated platforms offering algorithmic strategies for trading*, moves in the **opposite** direction from a disclosure mandate: rather than requiring backtest disclosure, it directs stock brokers providing algorithmic trading services not to reference, directly or indirectly, the past or expected future return or performance of an algorithm, and not to associate with platforms doing so. *The circular number, date and title were confirmed on sebi.gov.in; its operative text was read through secondary reproductions rather than the primary PDF, and the framework has been revised since — a subsequent SEBI framework contemplates an exception for claims verified by a designated verification agency. Confirm the current position with SEBI or the exchanges before relying on this.*

Do not summarise this area as an intersection with "institutional backtesting validation
standards, GIPS, and SEBI / SEC backtesting disclosure rules." That framing is unsourced and
wrong about SEBI, whose algo-trading circular restricts performance claims rather than requiring
backtest disclosure. Work from the provision-level statements above instead.
