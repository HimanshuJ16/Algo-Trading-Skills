# Pre-Flight Checklist: Benchmark Selection

## Before running the screen
- [ ] Were the candidate benchmarks — and the selection rule — written down *before* seeing the results?
- [ ] Are strategy and benchmark returns exactly aligned on dates, with no off-by-one shift?
- [ ] Are benchmark returns **total return** (including dividends/coupons), not price-only?
- [ ] Does the candidate list include a benchmark matching the strategy's sector, style, or factor bias — and a risk-free series if the strategy is market-neutral?
- [ ] Does `periods_per_year` match the return frequency (252 daily / 52 weekly / 12 monthly / 365 crypto)?

## Reading the results
- [ ] Is the top candidate's `beta` near 1.0, or is it right-shape-wrong-scale?
- [ ] Is `r_squared` high enough that the benchmark actually explains the strategy's variance?
- [ ] Have you checked whether `correlation`/`beta` came back `nan` (zero-variance benchmark) before quoting them?
- [ ] Is the information ratio finite — and if `±inf`, do you understand why active risk is zero?
- [ ] Is the sample long enough for these estimates to mean anything? Two observations produce a correlation of exactly ±1 by construction.

## Before reporting against it
- [ ] Is the chosen benchmark investable, unambiguous, and measurable — not just the best statistical fit?
- [ ] Are the `ddof` and `periods_per_year` conventions recorded alongside the reported tracking error and information ratio?
- [ ] Is the benchmark frozen for the evaluation period, with any future change documented and justified?
- [ ] Are you reporting *active return*, and not calling it alpha? Alpha requires a beta adjustment this screen does not perform.
