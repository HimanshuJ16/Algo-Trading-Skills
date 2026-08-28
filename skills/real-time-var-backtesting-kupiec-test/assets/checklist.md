# Pre-Flight Checklist — Kupiec POF VaR Backtest

## Input integrity

- [ ] Is the observation window $T \ge 250$ (approximately twelve months, per bcbs22 / MAR32.18)? If shorter, is the result explicitly flagged as below the Basel minimum rather than reported as authoritative?
- [ ] Is the exception count the **greater** of the actual-P&L and hypothetical-P&L counts (MAR32.18(1))?
- [ ] Are sessions with a missing P&L or missing risk measure counted as exceptions (MAR32.18(2)), not dropped from $T$?
- [ ] Does the window end at the last **completed** session, with no partial day included?
- [ ] Do $T$ and $x$ satisfy $T \ge 1$, $0 \le x \le T$, both integers — and does the code **raise** rather than return a passing result when they do not?

## Statistical test

- [ ] Is the expected exception rate $p = 1 - \text{confidence\_level}$, matching the VaR level actually being tested (0.01 for 99%, 0.05 for 95%)?
- [ ] Is `alpha` the **statistical significance level** (0.05), not the VaR confidence level (0.99)?
- [ ] Is the reported statistic the Kupiec $LR_{\text{POF}}$ — not an exact binomial p-value relabelled as Kupiec?
- [ ] Is the p-value computed as $\operatorname{erfc}(\sqrt{LR/2})$ and **not** $\exp(-LR/2)$? Sanity check: $LR = 3.841459 \Rightarrow p = 0.0500$, never $0.1465$.
- [ ] Is $LR_{\text{POF}}$ evaluated in log space so $p^x$ cannot underflow at large $x$?
- [ ] Does the rejection flag agree with the reported p-value (both from the same rule)?

## Interpreting the result

- [ ] Is the rejection **direction** checked before escalating? A rejection with $\hat\pi < p$ means the model is too conservative, not that risk is understated.
- [ ] Is a non-rejection documented as weak evidence rather than model validation, given the test's ~65% detection rate for a 3%-as-1% VaR at one year?
- [ ] Has a separate **independence / clustering** test been run (Christoffersen 1998 or Christoffersen–Pelletier 2004)? POF cannot see clustering.

## Basel zone classification

- [ ] Are zone boundaries derived from the exact binomial CDF (amber at $P(X\le x)\ge 95\%$, red at $\ge 99.99\%$) rather than by linearly rescaling $x$ to a 250-day equivalent?
- [ ] At $T=250$, $p=0.01$, do the boundaries come out as green 0–4, amber 5–9, red 10+?
- [ ] Is the MAR32.9 multiplier attached **only** on the published basis (250 observations, 99% coverage), and `None` otherwise?
- [ ] Is the in-force MAR32.9 **total** multiplier (1.50–2.00) kept distinct from the 1996 bcbs22 **increment** (0.00–1.00)? They must never be summed.
- [ ] Is it understood that a green zone only means "no supervisory add-on", not "model calibrated"?

## Audit trail

- [ ] Does the output carry both verdicts separately — the two-sided Kupiec result and the one-sided Basel zone — rather than one collapsed boolean?
- [ ] Are $T$, $x$, expected rate, observed rate, $LR$, p-value, zone, cumulative probability, and multiplier applicability all persisted for review?
- [ ] Is every exception documented with an explanation, as a standing requirement (MAR32.12)?
- [ ] Has the applicable **national transposition** of the Basel rules been confirmed, rather than assuming the BCBS text applies directly?
