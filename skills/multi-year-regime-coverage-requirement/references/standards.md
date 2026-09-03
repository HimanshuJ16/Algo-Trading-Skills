# Standards — multi-year-regime-coverage-requirement

## Configuration defaults (calibrate before use)

These are the engine's defaults, **not** industry standards and **not** regulatory
minimums. No regulator, exchange or standards body publishes a mandatory backtest length,
a mandatory number of market regimes, or a maximum per-regime drawdown. The right values
depend on the strategy's holding period, the instrument's history, and the firm's risk
appetite. Calibrate each, and record the values alongside the audit report — a
threshold-dependent audit is not reproducible without them.

| Parameter | Default | What it actually does |
|---|---|---|
| `min_required_years` | $3.0$ | Duration floor, evaluated as `bars / bars_per_year` **before rounding**. On breach: `Insufficient duration`. |
| `min_required_regimes` | $3$ | How many regimes must clear `min_bars_per_regime`. Cannot exceed 4 — only four regimes exist. On breach: `Insufficient regimes`. |
| `min_bars_per_regime` | $21$ | Bars a regime needs before it counts toward coverage and before its Sharpe is reported. ~one trading month of daily data. Without it, two stray bars satisfy a three-regime rule. |
| `max_allowed_regime_drawdown_pct` | $25.0\%$ | Within-episode drawdown limit, per regime. On breach: `REGIME VETO`. Compared unrounded; exactly at the limit is not a breach. |
| `bars_per_year` | $252$ | Trading days per year for **daily** bars. Drives the duration gate and the Sharpe annualization. Must be overridden for any other frequency. |
| `window_size` | $20$ | Trailing window used to classify each bar. The first `window_size` bars are `UNCLASSIFIED`. |
| `high_vol_annualized_threshold` | $0.35$ | Annualized window volatility above which the bar is bucketed `HIGH_VOLATILITY_CRASH`. Heuristic, tuned for daily equity-index bars. |
| `trend_threshold_pct` | $0.03$ | Absolute window price change separating trend from range, applied symmetrically. Heuristic. |

## Regulatory position (verified — read this before citing anything)

**Nothing in MiFID II, RTS 6, or SEC Rule 15c3-5 mandates a minimum backtest history or
multi-regime coverage.** The closest anchor is a qualitative testing obligation:

| Claim | Source | Status |
|---|---|---|
| Firms must establish testing methodologies ensuring an algorithm "does not contribute to disorderly trading conditions, continues to work effectively in stressed market conditions" | Commission Delegated Regulation (EU) 2017/589 (RTS 6), Art. 5 — [EUR-Lex CELEX:32017R0589](https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng) | Mandatory (EU/EEA investment firms engaged in algorithmic trading). **Qualitative — no numeric history minimum.** |
| "RTS 6 (and 7) require investment firms to conduct conformance testing, stress testing, and scenario analysis." | ESMA, *Supervisory Briefing on Algorithmic Trading in the EU*, ESMA74-1505669079-10311, 26 Feb 2026, ¶25 | Supervisory guidance, not a rule. Confirms the three testing categories. |
| Scope, frequency and intensity of testing "vary significantly across the industry"; ESMA "recognises the need for proportionality in applying the testing provisions" | ibid. ¶26 | Guidance. Argues **against** presenting any fixed numeric threshold as an industry standard. |
| RTS 6 Art. 10 stress testing means system **capacity**: withstanding "twice the volume of messages or trades processed in the previous 6 months" | ibid. ¶32 | Mandatory. **Important:** RTS 6 "stress testing" is a throughput obligation, *not* a requirement to backtest strategy P&L across market regimes. Do not cite Art. 10 in support of this skill's thresholds. |
| Retesting is required after any "material change or substantial update", including changes to risk-control thresholds and retraining of ML components | ibid. ¶30–31 | Guidance. Relevant: changing this engine's thresholds is itself a change that warrants a re-audit. |

The defensible framing is therefore: multi-regime coverage is a **house validation
standard** that supports the RTS 6 Art. 5 obligation to show an algorithm works under
stressed conditions. It is not itself a regulatory requirement, and the numbers above are
not derived from one.

## Quantitative definitions (verified against primary sources)

| Fact | Source | Applied here |
|---|---|---|
| Ex-post Sharpe ratio = mean differential return ÷ its standard deviation, using "the formula for the standard deviation of a population, taking the observations as a sample" | Sharpe, W.F. (1994), "The Sharpe Ratio", *Journal of Portfolio Management* 21(1) — [author's text](https://web.stanford.edu/~wfsharpe/art/sr/sr.htm) | `_population_stdev` uses the population divisor deliberately, matching the source. Inputs are treated as **differential** returns; a non-zero risk-free rate must be subtracted upstream. |
| The $\sqrt{T}$ annualization holds only "under simple conditions with zero serial correlation"; compounding and serial correlation complicate it | ibid. | Regime Sharpe is annualized by $\sqrt{F}$ and reported as a **comparative indicator only**. |
| Serial correlation can overstate an annualized Sharpe ratio "by as much as 65 percent"; correct annualization under stationary (non-IID) returns requires a different adjustment | Lo, A.W. (2002), "The Statistics of Sharpe Ratios", *Financial Analysts Journal* 58(4) — [CFA Institute](https://rpc.cfainstitute.org/research/financial-analysts-journal/2002/the-statistics-of-sharpe-ratios) | A regime bucket is a set of non-contiguous bars selected *conditional on the price path* — close to the worst case for the IID assumption. Do not present these as defensible annualized statistics. |
| A bear market is a broad market index falling **20% or more over at least a two-month period**; a correction is a reversal of at least 10% | [SEC, Investor.gov glossary — "Bear Market"](https://www.investor.gov/introduction-investing/investing-basics/glossary/bear-market); [FINRA, "Key Terms for Tough Times"](https://www.finra.org/investors/insights/key-terms-tough-times-vocabulary-stressed-markets) | The engine's `BEAR_MARKET` label (a 20-bar move below $-3\%$) is **not** this definition. The four labels are engine-internal buckets and must not be reported as conventional market conditions in external documents. |

The classification scheme itself — threshold rules on rolling volatility and window price
change — is a heuristic chosen for transparency and reproducibility. It is not a published
method. Markov regime-switching models (Hamilton, 1989) are the standard academic
alternative and will disagree with these buckets; if an auditor expects one, this engine
is not a substitute.

## Known limitations

- **Bar frequency cannot be inferred** from a price list. `bars_per_year` governs both the
  duration gate and the annualization; leaving it at the daily default while feeding
  1-minute bars reports one year of data as ~390 years of coverage.
- **Calendar gaps are invisible.** Duration is a bar count, not a timestamp span.
- **Drawdown is per-regime, not portfolio-level.** `max_drawdown_pct` is the worst decline
  within one contiguous episode of a regime; `concatenated_drawdown_pct` chains separated
  episodes and describes a decline no account experienced.
- **No regime-transition analysis.** Bars are bucketed, not sequenced.
- **Passing is necessary, not sufficient.** The audit is silent on look-ahead bias,
  overfitting, and capacity.
