---
name: factor-research-multiple-testing-correction
description: >-
  Use when a research pipeline screens many candidate factors and selection rests on
  t-statistics, applying Bonferroni, Holm and Benjamini-Hochberg false discovery
  corrections plus the Harvey-Liu haircut.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: quant-research-alt-data
  tags: factor-research, multiple-testing-correction, p-hacking, fdr, benjamini-hochberg, benjamini-yekutieli, harvey-liu-zhu
  brokers_frameworks: "Harvey-Liu-Zhu (2016) RFS; Benjamini-Hochberg (1995); Benjamini-Yekutieli (2001); Holm (1979); Python Standard Library"
  version: "1.1.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when a research pipeline screens more than one candidate factor and the
selection decision rests on t-statistics or p-values: factor discovery sweeps, signal
libraries, parameter grids evaluated as separate hypotheses, and any promotion gate
where "it was significant in the backtest" is the argument for going live.

Screening $M$ candidates at the single-test cutoff ($p \le 0.05$, $|t| \ge 1.96$) is
expected to produce $0.05 \times M$ significant results from pure noise — 50 of them
out of 1,000. Harvey, Liu and Zhu (2016) argue this is exactly how the published
factor zoo was assembled, and recommend that a newly discovered factor clear
$|t| \ge 3.0$.

## When NOT to Use

- **A single pre-registered hypothesis.** With $M = 1$ every procedure here collapses
  to the raw p-value; the correction adds nothing.
- **As a fix for a broken backtest.** These are statistical corrections. They cannot
  see look-ahead leakage, a survivorship-biased universe, or overlapping-return
  t-statistic inflation. A corrected t-statistic on a leaked signal is still leaked —
  see `lookahead-bias-elimination`.
- **As a per-factor probability.** FDR is an expectation over the batch. Controlling
  FDR at 5% says nothing about whether any *particular* survivor is real.
- **Ranking or sizing.** These procedures answer accept/reject, not "how much alpha"
  or "how much capital".

## Prerequisites

- Per-candidate test results: factor ID, name, sample size, raw t-statistic, and the
  **two-sided** p-value corresponding to that t-statistic. `two_sided_p_value_from_t`
  is provided when only t-statistics are on hand.
- $\alpha$ (family-wise level, default $0.05$) and $q^*$ (FDR level, default $0.05$).
- An honest count — or defensible upper bound — of the tests actually conducted,
  including the ones that were discarded and never written down.

## Workflow

1. **Establish $M$ before looking at any result.** $M$ is the number of tests
   *conducted*, not the number *kept*. If 40 specifications were run and 6 survived
   into the results file, $M = 40$, not 6. Pass the true count as
   `total_tests_conducted`; leaving it to default to `len(factors)` does not make the
   audit conservative, it makes it optimistic, and it is the specific bias HLZ
   document. If the true count is genuinely unknown, use a defensible upper bound and
   record how it was derived.
2. **Check that each p-value matches its t-statistic and is two-sided.** The HLZ
   hurdle is stated on $|t|$; every other rule here is stated on $p$. If they
   disagree, the report contradicts itself. The engine raises on p-values outside
   $[0, 1]$ or non-finite t-statistics, and logs a warning when a p-value falls below
   the normal two-sided p-value implied by its t-statistic — impossible for any
   t-test, since Student-t tails are fatter than normal tails at every finite df.
3. **Decide FWER or FDR before seeing the counts, not after.** FWER (Bonferroni,
   Holm) bounds the probability of *even one* false discovery — appropriate when a
   single bad factor going live is costly. FDR (BH, BHY) bounds the *expected
   proportion* of false discoveries among those accepted — appropriate for a research
   funnel feeding further validation. Picking whichever gives more survivors is
   itself a form of p-hacking.
4. **On a correlated factor zoo, read BHY, not BH.** BH controls FDR under
   independence and under positive regression dependency (Benjamini and Yekutieli
   2001, Thm 1.2). Value, quality and low-volatility variants built from overlapping
   signals satisfy neither. BHY divides the threshold by
   $c(M) = \sum_{j=1}^{M} 1/j$, restoring FDR control under arbitrary dependence
   (ibid., Thm 1.3) — this is the FDR procedure HLZ themselves apply. At $M = 100$,
   $c(M) \approx 5.19$: BHY is roughly five times stricter than BH, and that gap is
   the price of the guarantee. The engine reports both so the cost is visible.
5. **Apply the HLZ hurdle as an independent cross-check, not a substitute.** The
   $|t| \ge 3.0$ hurdle is a fixed benchmark calibrated on the published factor zoo;
   it does not depend on this batch's $M$. Treat disagreement between it and the FDR
   verdict as a signal to look harder, not as a tie to break by preference.
6. **Read the adjusted p-values, not only the flags.** `adjusted_p_value_bh <= q` is
   exactly equivalent to `is_bh_fdr_significant`, for every procedure. The adjusted
   value is the smallest level at which the factor would be accepted, so it shows how
   much slack a survivor has left.
7. **Record the audit with the batch.** Persist $M$, $\alpha$, $q^*$, $c(M)$ and the
   per-factor adjusted p-values alongside the promotion decision. Recomputing an
   audit later with a different $M$ silently changes every verdict.

> Full procedure: see `references/workflows.md`.
> Sourced thresholds and their provenance: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Setting $M$ to the number of factors you kept.** The 6 survivors written to the
  results file were selected out of 40 runs; correcting for $M = 6$ leaves the
  selection bias entirely intact and produces a report that looks rigorous while
  correcting for almost nothing.
- **Reporting BH adjusted p-values without the monotonicity step.** Computing
  $M p_{(i)} / i$ per rank in isolation yields adjusted p-values that are non-monotone
  in rank and that can exceed $q^*$ for factors the step-up rule *accepts*. A
  downstream filter on `adjusted_p <= q` then silently drops factors the audit
  declared significant. The correct value is the running minimum
  $\min_{j \ge i} \min[M p_{(j)} / j,\ 1]$.
- **Using plain BH on a correlated factor zoo and calling it "the HLZ standard".** BH
  is not what HLZ apply; BHY is. On HLZ's own ten-test example BH accepts all ten
  while BHY accepts six.
- **Letting a NaN p-value through.** It compares `False` against every threshold, so
  it is never flagged significant — but it also breaks the sort the rank-based
  procedures depend on, corrupting the verdicts of every *other* factor in the batch.
  The engine rejects it rather than degrading quietly.
- **Reading `false_discoveries_filtered_count` as a count of proven false factors.**
  It counts raw-significant candidates that BH did not confirm. The procedure never
  identifies *which* discoveries are false.
- **Rejecting genuine alpha with Bonferroni on a correlated family.** Bonferroni
  assumes the worst about dependence in the direction of strictness and is uniformly
  dominated by Holm. If FWER is the requirement, use Holm.
- **Re-running the audit with a friendlier $M$ or $q^*$ after seeing the result.**
  That is the p-hacking this skill exists to stop, moved up one level.

## Verification

`scripts/test_factor_research_multiple_testing_correction.py` (37 tests) anchors
correctness on values published independently of this implementation:

- **HLZ Table 4 reproduction.** The paper's worked ten-test example, whose discovery
  counts are published: single tests 10, Bonferroni 3, Holm 4, BHY 6 — including
  which test IDs are in each rejection set. `harmonic_sum(10) = 2.928968` reproduces
  the paper's rank-1 BHY threshold of 0.17%.
- **HLZ hurdle p-values.** `two_sided_p_value_from_t` reproduces the paper's
  published mappings: $t = 3.00 \to 0.27\%$, $t = 2.78 \to 0.54\%$,
  $t = 3.39 \to 0.07\%$.
- **Monotonicity regression.** With $p = (0.010, 0.012)$, $M = 2$, $q^* = 0.015$, the
  step-up rule accepts both; the un-monotonised formula reports an adjusted p-value of
  0.020 for rank 1, contradicting that acceptance. The test asserts the flag and the
  adjusted p-value agree, and fails against the un-monotonised formula.
- **Pure-noise zoo.** 100 tests on the deterministic uniform grid
  $p = 0.005, 0.015, \dots, 0.995$ — the spread expected when every null is true. Raw
  screening accepts 5; Bonferroni, Holm, BH, BHY and the HLZ hurdle each accept 0.
- **Tie handling, input-order independence, boundary p-values, NaN/out-of-range/
  duplicate-ID rejection, the `total_tests_conducted` override, and determinism.**

Run: `python -m unittest discover -s skills/factor-research-multiple-testing-correction/scripts`

## Related Skills

- `research-idea-pipeline-tracking-and-prioritization` — tracks how many hypotheses
  were actually tried, which is where a defensible $M$ comes from.
- `lookahead-bias-elimination` — the failure mode no multiple-testing correction can
  detect.
- `walk-forward-validation-setup` — out-of-sample confirmation for factors that
  survive this audit.
- `backtest-parameter-sensitivity-analysis` — a parameter sweep is a family of
  hypotheses and should be counted into $M$.
- `backtesting-alt-data-strategies-with-realistic-availability-lag`
