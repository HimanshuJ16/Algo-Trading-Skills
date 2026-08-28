# Workflows for Real-Time VaR Backtesting (Kupiec POF + Basel Zone)

## 1. Input collection and validation

1. Assemble the observation window: $T$ observations of one-day P&L against the
   corresponding one-day VaR forecast, ending at the last **completed** session.
2. Count exceptions $x$. Under MAR32.18 the count is the **greater** of the actual-P&L
   and hypothetical-P&L exception counts; a session where either the P&L or the risk
   measure is unavailable counts as an exception, not as a missing observation.
3. Validate before computing. $T < 1$, $x < 0$, $x > T$, or a non-integer count is a
   broken pipeline and must raise.
   - **Do not** substitute a passing result for a missing window. Returning
     "model accepted" when $T = 0$ converts a feed outage into a silent all-clear on a
     control that exists to catch under-capitalisation.
4. Record whether $T \ge 250$. Below that, results are produced but flagged: the
   $\chi^2$ approximation is asymptotic and the test's power is already weak at 250.

## 2. Kupiec POF likelihood ratio

1. Set $p = 1 - \text{confidence\_level}$ (0.01 for 99% VaR).
2. Compute in log space:
   $$LR_{\text{POF}} = -2\Big[(T-x)\ln(1-p) + x\ln p - (T-x)\ln(1-\hat\pi) - x\ln\hat\pi\Big], \quad \hat\pi = x/T$$
   - Use `math.log1p(-p)` rather than `log(1-p)` for accuracy at small $p$.
   - Handle $x = 0$ as $-2T\ln(1-p)$ and $x = T$ as $-2T\ln p$; these are the analytic
     limits, not special-case fudges.
   - Clamp to $\ge 0$: the unrestricted MLE maximises the likelihood by construction, so
     a negative value is floating-point noise around $\hat\pi = p$ that would otherwise
     propagate into $\sqrt{\cdot}$.
3. Convert to a p-value: $\operatorname{erfc}(\sqrt{LR/2})$.
   - **Not** $\exp(-LR/2)$ — that is the $\chi^2_2$ survival function and inflates
     p-values roughly threefold near the decision boundary.

## 3. Hypothesis evaluation

1. Reject $H_0$ when p-value $< \alpha_{\text{stat}}$ (0.05 by convention; BCBS does not
   mandate a Kupiec threshold).
2. Determine the direction before acting on a rejection:
   - $\hat\pi > p$ → the model **understates** risk. Escalate: recalibrate, and expect a
     supervisory add-on if the zone is amber or red.
   - $\hat\pi < p$ → the model **overstates** risk. This is a capital-efficiency finding,
     not a breach event. Zero exceptions in 250 days rejects at $p = 0.0250$.
   - Compare $\hat\pi$ and $p$ with a tolerance, not exact equality: `1.0 - 0.99` is
     `0.010000000000000009` in binary floating point.
3. Do not read a non-rejection as validation. At $T = 250$ the test misses a model
   reporting 3% VaR as 1% about 35% of the time.

## 4. Basel supervisory zone classification

1. Find the boundaries from the exact binomial CDF, per the bcbs22 Table 2 note:
   - amber starts at the smallest $x$ with $P(X \le x) \ge 0.95$;
   - red starts at the smallest $x$ with $P(X \le x) \ge 0.9999$.
   - At $T=250$, $p=0.01$ this yields $(5, 10)$, matching the published table.
   - Clamp the amber boundary to at least one exception. On a degenerate window the raw
     rule can place it at zero — at $T=1$, $P(X \le 0) = 0.99$ already clears 95% — and
     the zones penalise an *excess* of breaches, so "zero breaches, amber" is a sample-
     size artifact, not a finding. This clamp does not move the published boundaries.
2. Accumulate the CDF in log space so the binomial coefficient does not overflow on the
   multi-thousand-observation windows a real-time backtester builds up.
3. **Do not linearly rescale** the exception count to a 250-day equivalent. The binomial
   tail is not linear in $T$: at $T = 1000$ the correct amber boundary is 15, whereas
   rescaling implies 20 — a two-zone misclassification in the range that matters.
4. Attach the MAR32.9 multiplier only at $T = 250$ and 99% coverage. Off that basis,
   report `None` and say why; BCBS publishes no multiplier steps for other windows.

## 5. Audit report output

Return a structured result carrying **both** verdicts plus enough context to reconstruct
them: $T$, $x$, expected and observed exception rates, $LR_{\text{POF}}$, the p-value,
the rejection flag and its direction, the zone, the exact cumulative probability, the
multiplier (or its absence and the reason), and the below-minimum-window flag.

Never collapse the two verdicts into a single boolean. A green zone with a Kupiec
rejection on the conservative side, and an amber zone with no Kupiec rejection, are both
routine outcomes; a single flag cannot express either.

## 6. Follow-up when a model is rejected or lands in amber/red

1. Run an **independence** test on the breach sequence (Christoffersen Markov or
   Christoffersen–Pelletier duration). Clustering and miscoverage are separate failures
   with different remedies.
2. Disaggregate: bcbs22 Sec. 3(e) singles out backtests over subsets of the trading
   portfolio as the most useful evidence when arguing an amber-zone result.
3. Document every exception with an explanation (MAR32.12) — this is a standing
   requirement, not something to assemble once a zone breach occurs.
