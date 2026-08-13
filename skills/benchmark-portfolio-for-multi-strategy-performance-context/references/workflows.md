# Benchmarking Workflow

## 1. Assemble the portfolio series

Aggregate the per-period **net** returns of your multi-strategy portfolio — after fees, financing,
and transaction costs. Use simple (arithmetic) returns, not log returns; the Beta/Alpha
decomposition below is linear in simple returns.

## 2. Choose and assemble the benchmark series

Retrieve the **total** returns (including income) of your policy benchmark. Common choices:

| Fund mandate | Benchmark |
|---|---|
| Relative return, long-only equity | Broad equity total-return index (e.g. S&P 500 TR) |
| Multi-asset | Blended index (label it a *custom benchmark*; see `references/standards.md`) |
| Absolute return / market-neutral | Cash or risk-free rate |
| "Is the complexity worth it?" | The simple alternative the portfolio is meant to beat — a static 60/40 blend or an equal-weight sleeve |

A long-only equity index is the wrong benchmark for a market-neutral book: the resulting Beta is
near zero by construction and the "Alpha" is just the portfolio's total return less $R_f$.

## 3. Align the two series by date

This is the step that most often corrupts the result. A single missing benchmark day shifts every
subsequent observation by one period, which destroys the covariance without producing any obviously
wrong-looking number. Verify equal lengths **and** identical date indices. Do not forward-fill a
missing benchmark observation to force the lengths to match — drop the date from both series or
reject the run.

`evaluate_performance` rejects length mismatches, non-1D input, non-numeric input, and any NaN/Inf
value with a `BenchmarkingError`. It cannot detect a misalignment that happens to preserve length,
so date-index equality is the caller's responsibility.

## 4. Instantiate and evaluate

```python
from multi_strategy_benchmarker import BenchmarkingError, MultiStrategyBenchmarker

bench = MultiStrategyBenchmarker(risk_free_rate_annual=0.04, periods_per_year=252)
try:
    stats = bench.evaluate_performance(portfolio_returns, benchmark_returns)
except BenchmarkingError as exc:
    ...  # bad input: fix the data, do not fall back to a partial result
```

Set `periods_per_year` to match the return frequency (252 daily, 52 weekly, 12 monthly). Leaving it
at 252 for a monthly series overstates annualized Alpha by roughly 21x.

## 5. Read the output

| Key | Meaning |
|---|---|
| `Beta` | Market exposure. `0.0` against a constant benchmark is a **convention**, not a measurement — check `Warnings`. |
| `Correlation` | `NaN` if either series is constant. |
| `Annualized Alpha` | Jensen's Alpha, arithmetically annualized. Not a CAGR. |
| `Tracking Error (Ann)` | $\sqrt{252}$-scaled standard deviation of active return. |
| `Information Ratio` | `NaN` when tracking error is zero — undefined, not zero. |
| `Active Return (Ann)` | Annualized portfolio return less annualized benchmark return. |
| `Active Return t-stat` | $\mathrm{IR} \times \sqrt{\text{years}}$. Statistical support for the active return. |
| `Observations` / `Years` | Sample size. Always report these next to the IR. |
| `Warnings` | Every caveat that applied. Empty list means none. **Read this before quoting any number.** |

Undefined metrics are `float('nan')`, never `0.0`. If you serialize the result, note that
`json.dumps` emits a bare `NaN` literal, which is **not** valid JSON per RFC 8259 and will be
rejected by strict parsers in other languages. Replace NaN with `null` (or omit the key and rely on
`Warnings`) before writing a report file: `json.dumps(stats, allow_nan=False)` will raise, which is
a useful guard.

## 6. Interpret

1. **Check `Warnings` first.** A `NaN` metric with an unread warning is how a degenerate result gets
   reported as a real one.
2. **Check `Observations` / `Active Return t-stat` second.** An IR of 1.8 over 40 trading days
   ($t \approx 0.7$) is noise. See `references/standards.md` for the IR–t relationship.
3. **Then check Beta.** High Beta on a fund marketed as uncorrelated is hidden market exposure: the
   returns are replicable with a leveraged ETF at a fraction of the fee.
4. **Then check Tracking Error against the mandate**, not against a generic threshold. A tracking
   error that has drifted from 3% to 20% is a mandate breach even if Alpha is positive.
5. **Finally check Alpha** — and remember an unfunded leveraged clone of the benchmark shows
   $\alpha = +R_f$, not 0. Positive Alpha at $\beta \approx 2$ deserves a funding-cost check before
   it is called skill.
