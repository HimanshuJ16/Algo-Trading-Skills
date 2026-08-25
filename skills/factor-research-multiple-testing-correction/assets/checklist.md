# Pre-Flight Checklist — Factor Multiple Testing Correction

Sign off before any factor from this batch is promoted.

## Before running the audit

- [ ] Is $M$ the number of tests **conducted**, including discarded specifications,
      parameter-grid cells and the file drawer — not just the number recorded?
- [ ] If the exact $M$ is unrecoverable, is a defensible upper bound used, and is the
      derivation written down?
- [ ] Is `total_tests_conducted` passed explicitly whenever $M$ exceeds the number of
      results supplied? (The default is the optimistic case.)
- [ ] Were the error criterion (FWER or FDR) and the level ($\alpha$, $q^*$) fixed
      **before** any result was inspected?
- [ ] Are all p-values two-sided and consistent with their t-statistics? Were any
      t/p-consistency warnings investigated rather than ignored?
- [ ] Are raw t-statistics and p-values recorded for every candidate, including the
      ones that failed?

## Reading the result

- [ ] Was BHY, not plain BH, used as the FDR verdict for a correlated factor family?
- [ ] Do the counts satisfy Bonferroni ⊆ Holm ⊆ BH and BHY ⊆ BH?
- [ ] Was the Harvey-Liu-Zhu $\lvert t \rvert \ge 3.0$ hurdle checked as an
      independent cross-check against the published literature, rather than used to
      overrule an FDR rejection?
- [ ] Were disagreements between procedures resolved on stated grounds, not by picking
      the procedure with the most survivors?
- [ ] Is `false_discoveries_filtered_count` reported as *candidates removed for lack of
      evidence*, never as *factors proven false*?

## Before promotion

- [ ] Are $M$, $\alpha$, $q^*$, $c(M)$ and the per-factor adjusted p-values persisted
      with the promotion decision, so the audit can be reproduced?
- [ ] Has each survivor been checked for look-ahead bias, survivorship bias and
      overlapping-return t-statistic inflation? (No correction here detects those.)
- [ ] Is out-of-sample / walk-forward validation scheduled for every survivor?
- [ ] Has economic significance after transaction costs been assessed separately from
      statistical significance?
- [ ] Is the audit being run **once**, not re-run with a friendlier $M$ or $q^*$ after
      an unwelcome result?
