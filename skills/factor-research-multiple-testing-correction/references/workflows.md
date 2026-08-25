# Workflows — factor-research-multiple-testing-correction

Full procedure for auditing a batch of candidate factor test results. Formulas and
their sources are in `references/standards.md`.

## 0. Fix $M$ and the error criterion before looking at results

Both decisions must be made ex ante. Choosing $M$ or switching between FWER and FDR
after seeing which factors survive is p-hacking one level up, and it is invisible in
the output.

**Counting $M$.** $M$ is the number of hypotheses *tested*, not the number recorded:

| Source of tests | Counts toward $M$? |
|---|---|
| Factors in the results file | Yes |
| Specifications run and discarded | Yes |
| Each cell of a parameter grid evaluated as a separate hypothesis | Yes |
| Signals abandoned before a formal t-test was computed | Yes, if a selection decision was made on a performance number |
| Re-running the same specification on refreshed data | Once, unless the re-run is itself a selection opportunity |

If the exact count is unrecoverable, use a defensible upper bound and record how it was
derived. `research-idea-pipeline-tracking-and-prioritization` is where a trustworthy
count comes from — reconstructing $M$ after the fact almost always undercounts.

**FWER or FDR.** FWER (Bonferroni, Holm) bounds the probability of *even one* false
discovery; use it when a single bad factor reaching production is expensive. FDR
(BH, BHY) bounds the *expected proportion* of false discoveries among those accepted;
use it for a research funnel whose survivors face further out-of-sample validation.

## 1. Ingest and validate

Build one `CandidateFactorTest` per candidate with `factor_id`, `factor_name`,
`raw_t_stat`, the **two-sided** `raw_p_value`, and `sample_size`.

The engine rejects, with `ValueError` or `TypeError`:

- an empty batch;
- a p-value that is NaN, infinite, or outside $[0, 1]$;
- a non-finite t-statistic;
- a duplicate `factor_id` — the same factor counted twice inflates $M$ and shifts every
  rank-based threshold.

It logs a **warning** (not an error) when a supplied p-value falls below the normal
two-sided p-value implied by its t-statistic. Student-t tails are fatter than normal
tails at every finite degrees of freedom, so the normal p-value is a hard lower bound
for any t-test: a p-value below it means the pair is inconsistent, usually a one-sided
p-value or a p-value carried over from a different specification. It stays a warning
because bootstrap and permutation p-values legitimately need not match a t-statistic.

If only t-statistics are available, derive p-values with `two_sided_p_value_from_t`
rather than mixing conventions across the batch.

## 2. Run the audit

```python
engine = FactorMultipleTestingCorrectionEngine(
    alpha_target=0.05, fdr_q_target=0.05, hlz_t_threshold=3.0)
report = engine.audit_and_correct_factors(factors, total_tests_conducted=420)
```

The engine sorts by ascending p-value, computes Bonferroni, Holm, BH and BHY adjusted
p-values with their monotonicity steps, and derives every flag by comparing the
adjusted p-value against its level — so a flag can never disagree with its adjusted
p-value. It mutates the supplied objects in place and returns them ordered by p-value
in `report.audited_factors`.

## 3. Read the report

| Field | Meaning |
|---|---|
| `total_factors_tested` | $M$ actually used — the override when supplied, otherwise `len(factors)` |
| `factor_results_supplied` | How many results were passed in |
| `bhy_dependence_factor` | $c(M)$ |
| `raw_significant_count` | $p \le \alpha$, uncorrected — the count to compare against, not to act on |
| `bonferroni_significant_count` / `holm_significant_count` | FWER survivors |
| `bh_fdr_significant_count` / `bhy_fdr_significant_count` | FDR survivors |
| `hlz_t3_significant_count` | $\lvert t \rvert \ge$ hurdle; independent of $M$ |
| `false_discoveries_filtered_count` | Raw-significant candidates BH did not confirm. **Removed for lack of evidence, not proven false** — the procedure never identifies which discoveries are false. |
| `audit_notes` | One-line human-readable summary, also emitted at INFO |

Per factor: `p_value_rank`, the four `adjusted_p_value_*` values, and the five `is_*`
verdicts.

Expected orderings on any batch — useful as a sanity check:

    Bonferroni ⊆ Holm ⊆ BH        and        BHY ⊆ BH

## 4. Interpret disagreements

| Situation | Reading |
|---|---|
| BH accepts, BHY rejects | The result depends on the independence/PRDS assumption. On a correlated zoo, believe BHY. |
| FDR accepts, HLZ hurdle rejects | The factor clears this batch's bar but not the published-literature bar. Weak by HLZ standards; treat as a candidate for more evidence, not a discovery. |
| HLZ hurdle accepts, FDR rejects | $M$ for this batch is large enough to outweigh a $\lvert t \rvert \ge 3$ result. Check that $M$ is right before overriding — this is where an inflated $M$ shows up. |
| Everything rejects, raw testing accepted several | The expected outcome for a noise batch. It is the correct result, not a tuning failure. |
| Everything accepts including Bonferroni | Strong. Confirm the p-values are two-sided and that $M$ is not understated. |

## 5. Promote

Only factors that clear the pre-committed criterion proceed. Persist $M$, $\alpha$,
$q^*$, $c(M)$, the per-factor adjusted p-values and the promotion decision together:
an audit recomputed later with a different $M$ silently produces different verdicts,
and without the recorded $M$ the earlier decision cannot be reproduced.

Surviving this audit establishes that the result is unlikely to be multiplicity noise.
It establishes nothing else. Send survivors to out-of-sample validation
(`walk-forward-validation-setup`), leakage review (`lookahead-bias-elimination`) and
transaction-cost analysis before any capital decision.
