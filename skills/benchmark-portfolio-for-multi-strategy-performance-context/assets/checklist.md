# Pre-Flight Checklist

## Data integrity

- [ ] Are the portfolio and benchmark return series exactly the same length **and** aligned on identical date indices? (Equal length alone does not prove alignment.)
- [ ] Are both series free of NaN/Inf? (`evaluate_performance` raises `BenchmarkingError` if not — do not strip the values to force it through.)
- [ ] Are both series simple returns on the same frequency, and does `periods_per_year` match that frequency?
- [ ] Are portfolio returns **net** of fees, financing, and transaction costs?
- [ ] Is the benchmark a **total**-return series (including income), not price-only?

## Benchmark appropriateness

- [ ] Is the chosen benchmark appropriate for the fund's mandate (Absolute Return vs Relative Return), and was it specified in advance rather than chosen after seeing the results?
- [ ] If the benchmark is a blend, is it documented as a custom benchmark with its components and weights?
- [ ] Have you verified the risk-free rate is the annual figure the constructor expects, on the same basis as the return series?

## Statistical support

- [ ] Does the sample cover at least one year? (`Years` and `Observations` are in the output; a sub-year sample raises a warning.)
- [ ] Have you read `Active Return t-stat` alongside the Information Ratio? An IR without a sample size is uninterpretable.
- [ ] Are the active returns plausibly serially uncorrelated? Autocorrelated (smoothed / illiquid) marks make the $\sqrt{252}$ tracking-error annualization understate risk and inflate the IR.
- [ ] Do the calculated Beta and Alpha hold up over a multi-year horizon and across sub-periods, rather than resting on a single regime?

## Reading the result

- [ ] Have you read the `Warnings` list **before** quoting any figure from the output?
- [ ] For any `NaN` metric, have you identified which degeneracy caused it, rather than substituting 0?
- [ ] If Beta is reported as `0.0`, have you confirmed it was *measured* rather than set by the constant-benchmark convention?
- [ ] If Beta is materially above 0, have you checked whether the "Alpha" is just unfunded leverage ($\alpha = +R_f$ for a 2x unfunded clone)?
- [ ] Is the tracking error still within the fund's stated mandate, independent of whether Alpha is positive?
