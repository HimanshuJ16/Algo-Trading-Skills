# Workflow: Benchmark Selection

## 1. Specify candidates in advance

Write down the candidate benchmarks — and the rule for choosing among them — before
looking at the screen output. Gather returns for broad indices (SPY, QQQ, IWM),
sector/style ETFs (XLF, XLK), and the risk-free rate as appropriate to the mandate.
Screening a wide net and keeping whichever benchmark flatters the strategy defeats
the "specified in advance" property (see `references/standards.md`).

## 2. Use total-return series

Benchmark returns must include income (dividends, coupons). Comparing a strategy that
receives dividends against a price-only index manufactures fake outperformance of
roughly the dividend yield per year.

## 3. Align the series

Strategy and benchmark returns must be the same length and aligned date-for-date.
`BenchmarkSelector` enforces equal length and rejects NaN/Inf, but it cannot detect a
series that is the right length and shifted by one day — which would silently mix a
lead/lag into every statistic. Align on an explicit date index upstream.

## 4. Set the annualisation convention

`periods_per_year` must match the return frequency: 252 daily equities, 52 weekly,
12 monthly, 365 for 24/7 crypto. A monthly series left at the 252 default overstates
tracking error by √21 ≈ 4.6x. Set `ddof` to match your reporting convention
(default 1, sample).

## 5. Compute the screen

```python
selector = BenchmarkSelector(candidates, periods_per_year=252)
for m in selector.evaluate_benchmarks(strategy_returns):
    print(m.name, m.tracking_error, m.beta, m.r_squared, m.information_ratio)
```

Results come back sorted by ascending tracking error — closest-replicating benchmark
first.

## 6. Interpret, don't just take the top row

| Observation | What to do |
|---|---|
| Top candidate has `beta` far from 1.0 | Right exposure, wrong scale. Prefer a scaled/levered variant of the index, or accept the mismatch explicitly. |
| Top candidate has low `r_squared` | No candidate explains the strategy. Consider a multi-factor benchmark or an absolute-return (risk-free) comparator. |
| `correlation` is `nan` | The benchmark has zero variance (flat risk-free series). Tracking error is still meaningful; correlation and beta are not. |
| `information_ratio` is `±inf` | Zero active risk with non-zero active return — the strategy differs from the benchmark by a constant. Usually means the "benchmark" is the strategy itself plus a fee. |
| Strategy is market-neutral | Expect the risk-free rate to win on tracking error. That is the correct answer, not a bug. |

## 7. Confirm the qualitative properties

Before reporting performance against the winner, confirm it is investable,
unambiguous, measurable, and was specified in advance. The screen ranks statistical
fit only; it has no way to know whether the index can actually be held.

## 8. Freeze it

Record the chosen benchmark, the `periods_per_year` and `ddof` conventions, and the
date range used. Changing the benchmark after a bad quarter — or re-running the
screen and switching — is the failure mode this whole procedure exists to prevent.
