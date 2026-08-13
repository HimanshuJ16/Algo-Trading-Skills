# Deep Workflow Reference — backtest-reporting-standardized-tearsheet

This file holds the full technical procedure referenced by `SKILL.md`. Metric
definitions, conventions and sourcing live in `references/standards.md`.

## Full Procedure

1. **Aggregate returns at one consistent frequency.**
   Simple decimal returns, chronological, no gaps. Do not mix daily and intraday rows,
   and do not feed log returns — the equity curve compounds as $\prod(1+R_t)$.
   Filling a missing session with 0.0 is a modelling decision that lowers volatility and
   raises Sharpe; make it deliberately or drop the period.

2. **Set the annualization basis to match that frequency.**
   ```python
   gen = StandardizedTearsheetGenerator(
       risk_free_rate=0.04,     # annual, same basis as periods_per_year
       periods_per_year=252,    # 252 daily / 52 weekly / 12 monthly
   )
   ```
   This is the single most consequential setting. A trading-day series annualized with
   365 overstates volatility by about 20% and understates Sharpe by the same factor.

3. **Choose the Calmar convention before you publish, not after.**
   Default is $\text{CAGR}/|\text{maxDD}|$. Pass `calmar_uses_excess_return=True` for
   $(\text{CAGR}-r_f)/|\text{maxDD}|$. The report echoes the choice in
   `Calmar Convention`; carry that string into any comparison table.

4. **Generate, and let invalid input fail.**
   ```python
   report = gen.generate(returns)   # raises TearsheetError on bad input
   ```
   Non-finite values, returns below $-100\%$, fewer than 2 observations, and non-1-D
   input all raise rather than producing a plausible-looking report. An empty array
   returns `{}`.

5. **Read the qualifiers before the metrics.**
   - `Annualization Extrapolated` — True when the sample is shorter than one year.
     `Annualized Return` and `Annualized Volatility` are then projections. A three-day
     sample still produces a number; it just does not mean anything.
   - `Periods` — the observation count, needed to judge every other figure and to
     deflate the Sharpe if the parameters were selected by search.
   - `Calmar Convention` — which formula produced the Calmar value.

6. **Interpret degenerate values literally.**
   $\pm\infty$ and `nan` are information, not errors: infinite Sharpe means zero
   variance, `nan` profit factor means neither gains nor losses occurred. They are never
   reported as `0.0`, which would rank a flawless curve alongside a worthless one.

7. **Read drawdown as three numbers, not one.**
   Depth (`Max Drawdown`), how long it took to get there (`Max Drawdown Duration`), and
   how long it took to get back (`Max Drawdown Recovery Periods`, `None` if never). A
   shallow drawdown that never recovers is worse than a deep one that recovers in a week.

8. **Do not treat `Hit Rate` as a trade win rate.**
   It is the fraction of *periods* that closed positive. Convert the trade log to
   per-trade returns and set `periods_per_year` to the observed trade frequency if you
   need trade-level statistics.

9. **Record provenance next to the numbers.**
   Return frequency, `periods_per_year`, risk-free rate, Calmar convention, observation
   count, sample start and end, and whether costs and slippage were included. A tearsheet
   without these is not comparable to another tearsheet.

## Failure Modes Observed in Production

- **Drawdown measured from the first close instead of starting capital.** Returns
  $[-0.50, +0.10, +0.05]$ reported a maximum drawdown of $0.0$; three $-10\%$ periods
  reported $-19\%$ against a true $-27.1\%$. The equity curve must be seeded at 1.0.
- **A NaN producing a half-valid report.** Risk statistics go `nan` while hit rate and
  profit factor stay plausible, so the tearsheet looks partly fine and gets circulated.
- **One observation annualized.** A single $+5\%$ day compounds to over
  $20{,}000{,}000\%$ annualized, printed without qualification.
- **A return below $-100\%$.** Equity goes negative, the drawdown denominator flips sign,
  and the reported drawdown was $-151\%$ — worse than losing everything.
- **`0.0` for a degenerate ratio.** A zero-volatility, positive-return curve reported
  Sharpe $0.0$ and Calmar $0.0$, the worst possible scores for the best possible outcome.
- **Floating-point noise masquerading as volatility.** `numpy.std` of a constant series
  returns $\approx 10^{-18}$, so an unguarded division produced a Sharpe of
  $8.7\times10^{16}$ instead of infinity. Constancy is tested on the input, exactly.
- **Undocumented Calmar variant.** Silently subtracting $r_f$ made the number
  incomparable with every source using the more common convention.
- **Wrong annualization factor.** $\sqrt{365}$ on a trading-day series, or 252 left in
  place after switching to monthly bars.

## Production Implementation Reference

- Reference code: `scripts/tearsheet_generator.py` (`StandardizedTearsheetGenerator`,
  `TearsheetError`).
- Automated unit tests: `scripts/test_tearsheet_generator.py`.
