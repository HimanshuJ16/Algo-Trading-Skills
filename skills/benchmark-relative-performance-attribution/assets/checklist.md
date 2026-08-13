# Pre-Flight / Sign-off Checklist — benchmark-relative-performance-attribution

Use this before considering the skill's implementation complete, and before quoting any
attribution figure externally.

## Inputs

- [ ] **Date alignment:** $R_p$ and $R_b$ aligned on an explicit date index upstream — equal
      length is enforced by the engine, date alignment is not.
- [ ] **Total-return benchmark:** benchmark series includes dividends, not price-only.
- [ ] **Annualization factor:** matches the data frequency (252 / 52 / 12 / 365). A monthly
      series left at 252 is wrong by $4.6\times$.
- [ ] **Risk-free rate:** annual decimal, and the same $R_f$ used everywhere in the report.
- [ ] **No non-finite values:** confirmed the engine raised nothing on NaN/Inf.

## Alpha & beta

- [ ] **Beta measured, not assumed:** confirm no flat-benchmark fallback was silently
      applied — the engine raises on zero benchmark variance.
- [ ] **Alpha convention disclosed:** arithmetic annualization stated in the report;
      any comparison against `pyfolio`/`empyrical` accounts for their geometric convention.
- [ ] **Alpha is beta-adjusted:** active return ($R_p - R_b$) has not been reported as alpha.

## Tracking error & information ratio

- [ ] **$TE$ independently verified** against a sample standard deviation of active
      returns, annualized.
- [ ] **$IR$ numerator annualized** consistently with $TE$.
- [ ] **$t$-statistic recorded:** `information_ratio_t_stat` reported next to the IR, with
      the sample length in years. $\lvert t \rvert < 1.96$ recorded as such.
- [ ] **Sample length adequate:** more than 30 observations, and enough years that the IR
      gate is meaningful. Confirm no thin-sample warning was logged and ignored.

## Brinson-Fachler attribution

- [ ] **Single period only:** effects are for one period. If multiple periods are reported,
      a linking method (Cariño / Menchero / Frongello / GRAP) was applied — effects were
      **not** simply summed across time.
- [ ] **Start-of-period weights** used for both portfolio and benchmark.
- [ ] **Exhaustive partition:** both weight vectors sum to 1.0; a cash/other bucket carries
      any residual rather than being dropped.
- [ ] **Every weighted sector has an explicit return** — no silently defaulted 0.0.
- [ ] **$R_b$ derived, not pasted:** `total_benchmark_return` left as `None`, or confirmed
      to match the value implied by the benchmark inputs.
- [ ] **Reconciliation:** $\sum_i (A_i + S_i + I_i) = R_p - R_b$ verified.
- [ ] **Variant disclosed:** report states Brinson-Fachler (not BHB) and that interaction is
      reported separately rather than folded into selection.
- [ ] **Off-benchmark convention stated:** sectors with $w_b = 0$ are assigned $R_{b,i} = 0$.

## Scope

- [ ] **Single-factor limitation acknowledged:** any size/value/momentum tilt shows up as
      alpha here. A multi-factor check was run, or its absence is disclosed.
- [ ] **GIPS:** if the output appears in a GIPS Report, it is labelled *supplemental
      information*. This skill alone does not establish GIPS compliance.

## Automated testing

- [ ] **Test suite green:** `python -m unittest discover -s skills/benchmark-relative-performance-attribution/scripts`
      passes 100%.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
- Alpha annualization convention used: ___________________________
- Sample period and observation count: ___________________________
