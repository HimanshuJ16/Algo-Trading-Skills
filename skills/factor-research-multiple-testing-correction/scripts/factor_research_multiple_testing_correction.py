"""
factor-research-multiple-testing-correction: multiple-hypothesis-testing corrections
for factor / alpha discovery pipelines.

When M candidate factors are screened, the usual single-test cutoff (two-sided
p <= 0.05, |t| >= 1.96) is expected to declare 0.05 * M pure-noise factors
"significant". This module applies the four corrections used in Harvey, Liu and Zhu
(2016), "... and the Cross-Section of Expected Returns", *Review of Financial
Studies* 29(1), 5-68 (Sec. 3.4), plus that paper's headline t-statistic hurdle.

Notation: M is the total number of tests conducted; p_(1) <= ... <= p_(M) are the
ordered p-values; b is the rank; alpha_w is the family-wise level and alpha_d the
FDR level.

Procedures implemented (HLZ Sec. 3.4.1-3.4.3):

    Bonferroni (FWER, single-step, any dependence)
        reject  p_i <= alpha_w / M
        adj     p_bonf_i = min[M * p_i, 1]

    Holm (FWER, step-down, any dependence; Holm 1979)
        reject  H_(1)..H_(k-1) where k = min{b : p_(b) > alpha_w / (M + 1 - b)}
        adj     p_holm_(i) = min[max_{j<=i} (M - j + 1) * p_(j), 1]

    Benjamini-Hochberg (FDR, step-up; BH 1995)
        reject  H_(1)..H_(k) where k = max{b : p_(b) <= (b / M) * alpha_d}
        adj     p_bh_(i) = min_{j>=i} min[(M / j) * p_(j), 1]

    Benjamini-Hochberg-Yekutieli (FDR, step-up, arbitrary dependence; BY 2001)
        reject  H_(1)..H_(k) where k = max{b : p_(b) <= (b / (M * c(M))) * alpha_d}
        adj     p_bhy_(i) = min_{j>=i} min[(M * c(M) / j) * p_(j), 1]
        with    c(M) = sum_{j=1..M} 1/j

Why BHY and not only BH: BH controls FDR under independence (BH 1995) and under
positive regression dependency (Benjamini and Yekutieli 2001, Thm 1.2). A factor zoo
violates both -- value, quality and low-volatility variants built from overlapping
signals are strongly, and not only positively, dependent. BY 2001 Thm 1.3 shows the
c(M) inflation restores FDR control under *arbitrary* dependence, and BHY is the FDR
procedure HLZ actually apply to obtain their published hurdles. BH is retained
because it is the more familiar benchmark and bounds how much power the dependence
adjustment costs.

Adjusted p-values, not just flags: the adjusted p-value is the smallest level at
which a factor is declared significant, so `adjusted_p <= level` reproduces the
procedure's own accept/reject decision exactly, for every procedure here. The
monotonicity operators (the running minimum for the step-up procedures, the running
maximum for Holm) are what make that equivalence hold; dropping them yields adjusted
p-values that are non-monotone in rank and that can exceed the FDR target on factors
the step-up rule accepts.

Deviation from HLZ, deliberate: HLZ define the BHY recursion with base case
p_bhy_(M) = p_(M). That base case is only correct at c(M) = 1 (i.e. plain BH); left
as written it reports an adjusted p-value for the largest p that contradicts BHY's
own rejection rule whenever c(M) > 1. The general form min[(M * c(M) / M) * p_(M), 1]
is used instead, which reduces to HLZ's base case exactly when c(M) = 1.

Inputs and assumptions:

- **p-values must be two-sided and must correspond to `raw_t_stat`.** The HLZ hurdle
  is stated on |t| and every other rule here is stated on p; if the two disagree the
  report will contradict itself. `two_sided_p_value_from_t` is provided for callers
  who hold only t-statistics. A warning is logged when a supplied p-value is
  materially below the normal-approximation p-value implied by its t-statistic --
  Student-t tails are fatter than normal tails at every finite degrees of freedom, so
  the normal p is a hard lower bound for any t-test, and a p-value below it indicates
  a t/p mismatch rather than a small-sample effect.

- **M defaults to the number of results supplied, which is usually too small.** HLZ's
  central argument is that the recorded factor count understates the number of tests
  actually run: unreported specifications, discarded parameterisations and the
  file drawer never enter the tally, and every correction here scales with M.
  `total_tests_conducted` overrides M when the true trial count is known or can be
  bounded. Leaving it unset does not make the correction conservative -- it makes it
  optimistic.

- **When M exceeds the number of supplied results**, the supplied results are assumed
  to be the smallest p-values among all M tests (the reported winners), so their
  ranks are their global ranks. Under that assumption Bonferroni and Holm adjusted
  p-values are exact; the step-up (BH/BHY) adjusted p-values are upper bounds,
  because the running minimum cannot see the unsupplied larger-ranked terms. Upper
  bounds are the safe direction: they can understate significance, never overstate it.

Limitations, documented and deliberate:

- These are *statistical* corrections. They cannot detect a look-ahead bug, a
  survivorship-biased universe, or a backtest whose t-statistic is inflated by
  overlapping returns; a corrected t-statistic computed on a leaked signal is still
  a leaked signal.
- FDR is an expectation over the whole batch. Controlling FDR at 5% says nothing
  about whether any *particular* accepted factor is real.
- `sample_size` is recorded for the audit trail and is not used in the correction
  arithmetic; the corrections operate on the supplied t-statistics and p-values only.
- The engine mutates the `CandidateFactorTest` objects it is given, writing the
  per-procedure flags and adjusted p-values back onto them, and returns them ordered
  by ascending p-value.
"""
import logging
import math
from dataclasses import dataclass
from typing import List, Optional, Sequence

logger = logging.getLogger(__name__)

#: Headline hurdle recommended for a *newly discovered* factor in Harvey, Liu and Zhu
#: (2016), abstract and Sec. 5: "A new factor needs to clear a much higher hurdle,
#: with a t-statistic greater than 3.0." This is a research recommendation, not a
#: regulatory or exchange requirement. See references/standards.md.
HLZ_RECOMMENDED_T_HURDLE = 3.0

#: Relative tolerance for the t/p consistency warning. The normal two-sided p-value is
#: a strict lower bound on the two-sided p-value of any finite-df t-test, so a supplied
#: p-value below it is inconsistent. The tolerance exists only to absorb reporting
#: precision (published tables quote p-values to two significant figures), not to
#: absorb a genuine distributional difference.
P_VALUE_T_CONSISTENCY_TOLERANCE = 0.25


def harmonic_sum(m: int) -> float:
    """
    c(M) = sum_{j=1..M} 1/j, the Benjamini-Yekutieli (2001) Eq. 1 dependence factor.

    Reproduces HLZ Table 4, panel D: c(10) = 2.928968, so the BHY threshold at rank
    b = 1 with alpha_d = 5% and M = 10 is 0.05 / (10 * 2.928968) = 0.17%.
    """
    if m < 1:
        raise ValueError(f"m must be >= 1, got {m}.")
    return sum(1.0 / j for j in range(1, m + 1))


def two_sided_p_value_from_t(t_stat: float) -> float:
    """
    Two-sided p-value for `t_stat` under the standard normal approximation:

        p = erfc(|t| / sqrt(2))

    The normal approximation is what HLZ use to map their hurdles to p-values
    (t = 3.0 -> 0.27%, t = 2.78 -> 0.54%, t = 3.39 -> 0.07%), and it is accurate for
    the sample sizes typical of factor research. It is *anti-conservative* for small
    samples: a t-distribution with finite degrees of freedom has fatter tails, so the
    true p-value is always at least this large. Use an exact t-distribution CDF when
    degrees of freedom are small.
    """
    if not math.isfinite(t_stat):
        raise ValueError(f"t_stat must be finite, got {t_stat}.")
    return math.erfc(abs(t_stat) / math.sqrt(2.0))


@dataclass
class CandidateFactorTest:
    """
    One candidate factor's test result plus the verdict of each correction.

    `raw_p_value` must be the two-sided p-value corresponding to `raw_t_stat`.
    `sample_size` is recorded for the audit trail only. Every `is_*` flag and every
    `adjusted_p_value_*` field is overwritten by the engine on each audit run.
    """
    factor_id: str
    factor_name: str
    raw_t_stat: float
    raw_p_value: float
    sample_size: int
    is_raw_significant: bool = False
    is_bonferroni_significant: bool = False
    is_holm_significant: bool = False
    is_bh_fdr_significant: bool = False
    is_hlz_t3_significant: bool = False
    adjusted_p_value_bh: float = 1.0
    is_bhy_fdr_significant: bool = False
    adjusted_p_value_bonferroni: float = 1.0
    adjusted_p_value_holm: float = 1.0
    adjusted_p_value_bhy: float = 1.0
    p_value_rank: int = 0


@dataclass
class FactorMultipleTestingAuditReport:
    """
    Batch-level summary of one multiple-testing audit.

    `total_factors_tested` is M -- the trial count the corrections were computed over,
    which equals `total_tests_conducted` when that override is supplied and
    `factor_results_supplied` otherwise.

    `false_discoveries_filtered_count` counts factors that raw testing accepted but
    Benjamini-Hochberg did not. These are candidates *removed for lack of evidence
    after correction*; the procedure does not identify which discoveries are false, so
    this is not a count of confirmed false positives.
    """
    total_factors_tested: int
    alpha_target: float
    fdr_q_target: float
    raw_significant_count: int
    bonferroni_significant_count: int
    holm_significant_count: int
    bh_fdr_significant_count: int
    hlz_t3_significant_count: int
    false_discoveries_filtered_count: int
    audited_factors: List[CandidateFactorTest]
    audit_notes: str
    bhy_fdr_significant_count: int = 0
    factor_results_supplied: int = 0
    bhy_dependence_factor: float = 1.0
    hlz_t_threshold: float = HLZ_RECOMMENDED_T_HURDLE


def _step_up_adjusted(sorted_p: Sequence[float], m_tests: int, scale: float) -> List[float]:
    """
    Step-up adjusted p-values: adj_(i) = min_{j>=i} min[(m * scale / j) * p_(j), 1].

    `scale` is 1.0 for Benjamini-Hochberg and c(M) for Benjamini-Hochberg-Yekutieli.
    The running minimum enforces monotonicity in rank; without it the adjusted
    p-values do not reproduce the step-up rejection set and can be non-monotone.
    """
    n = len(sorted_p)
    adjusted = [1.0] * n
    running = 1.0
    for i in range(n, 0, -1):
        running = min(running, min(1.0, (m_tests * scale / float(i)) * sorted_p[i - 1]))
        adjusted[i - 1] = running
    return adjusted


def _holm_adjusted(sorted_p: Sequence[float], m_tests: int) -> List[float]:
    """
    Holm step-down adjusted p-values: adj_(i) = min[max_{j<=i} (m - j + 1) * p_(j), 1].

    The running maximum is what makes `adj_(i) <= alpha` equivalent to Holm's
    step-down rejection rule, and it also gives tied p-values identical verdicts
    instead of letting sort order decide.
    """
    adjusted = []
    running = 0.0
    for i, p in enumerate(sorted_p, start=1):
        running = max(running, (m_tests - i + 1) * p)
        adjusted.append(min(1.0, running))
    return adjusted


class FactorMultipleTestingCorrectionEngine:
    """
    Applies Bonferroni, Holm, Benjamini-Hochberg and Benjamini-Hochberg-Yekutieli
    corrections plus the Harvey-Liu-Zhu (2016) t-statistic hurdle to a batch of
    candidate factor test results.

    Stateless with respect to batches: every call recomputes all flags from the
    supplied results.
    """

    def __init__(
        self,
        alpha_target: float = 0.05,
        fdr_q_target: float = 0.05,
        hlz_t_threshold: float = HLZ_RECOMMENDED_T_HURDLE,
    ) -> None:
        if not 0.0 < alpha_target < 1.0:
            raise ValueError(f"alpha_target must be in (0, 1), got {alpha_target}.")
        if not 0.0 < fdr_q_target < 1.0:
            raise ValueError(f"fdr_q_target must be in (0, 1), got {fdr_q_target}.")
        if not math.isfinite(hlz_t_threshold) or hlz_t_threshold <= 0.0:
            raise ValueError(
                f"hlz_t_threshold must be a positive finite number, got {hlz_t_threshold}.")
        self.alpha_target = alpha_target
        self.fdr_q_target = fdr_q_target
        self.hlz_t_threshold = hlz_t_threshold

    @staticmethod
    def _validate_factors(factors: List[CandidateFactorTest]) -> None:
        """
        Rejects inputs that would silently corrupt the correction.

        A NaN p-value is the dangerous case: it compares False against every
        threshold, so it is never flagged significant, but it also breaks the sort
        that the step-up and step-down procedures depend on. `sorted()` would return
        a list that is not actually ordered, and the rank-based thresholds would then
        be applied to the wrong factors -- corrupting the verdicts of every *other*
        factor in the batch, not just the malformed one.
        """
        seen_ids = set()
        for f in factors:
            if not isinstance(f, CandidateFactorTest):
                raise TypeError(f"Expected CandidateFactorTest, got {type(f).__name__}.")
            if f.factor_id in seen_ids:
                raise ValueError(
                    f"Duplicate factor_id {f.factor_id!r}: the same factor counted twice "
                    "inflates M and distorts every rank-based threshold.")
            seen_ids.add(f.factor_id)
            if not math.isfinite(f.raw_p_value):
                raise ValueError(
                    f"Factor {f.factor_id!r} has non-finite raw_p_value {f.raw_p_value}.")
            if not 0.0 <= f.raw_p_value <= 1.0:
                raise ValueError(
                    f"Factor {f.factor_id!r} has raw_p_value {f.raw_p_value} outside [0, 1].")
            if not math.isfinite(f.raw_t_stat):
                raise ValueError(
                    f"Factor {f.factor_id!r} has non-finite raw_t_stat {f.raw_t_stat}.")
            implied_p = two_sided_p_value_from_t(f.raw_t_stat)
            if f.raw_p_value < implied_p * (1.0 - P_VALUE_T_CONSISTENCY_TOLERANCE):
                logger.warning(
                    "Factor %s: raw_p_value=%.6g is below the normal two-sided p-value "
                    "implied by t=%.4f (%.6g). Student-t tails are fatter than normal at "
                    "every finite df, so no t-test can produce a p-value this small for "
                    "this t-statistic. Check that the p-value is two-sided and matches "
                    "the t-statistic.",
                    f.factor_id, f.raw_p_value, f.raw_t_stat, implied_p)

    def audit_and_correct_factors(
        self,
        factors: List[CandidateFactorTest],
        total_tests_conducted: Optional[int] = None,
    ) -> FactorMultipleTestingAuditReport:
        """
        Runs every correction over `factors` and returns the batch audit report.

        `total_tests_conducted` sets M when the number of tests actually run exceeds
        the number of results supplied -- unreported specifications, discarded
        parameterisations, the file drawer. It must be at least `len(factors)`.
        Leaving it unset makes the correction *weaker*, not safer.

        Mutates the supplied objects in place and returns them ordered by ascending
        p-value in `audited_factors`.
        """
        if not factors:
            raise ValueError("Candidate factors list cannot be empty.")
        self._validate_factors(factors)

        n_supplied = len(factors)
        if total_tests_conducted is None:
            m_tests = n_supplied
        else:
            if total_tests_conducted < n_supplied:
                raise ValueError(
                    f"total_tests_conducted ({total_tests_conducted}) cannot be below the "
                    f"number of results supplied ({n_supplied}).")
            m_tests = total_tests_conducted

        c_factor = harmonic_sum(m_tests)
        sorted_factors = sorted(factors, key=lambda x: x.raw_p_value)
        sorted_p = [f.raw_p_value for f in sorted_factors]

        bonferroni_adj = [min(1.0, m_tests * p) for p in sorted_p]
        holm_adj = _holm_adjusted(sorted_p, m_tests)
        bh_adj = _step_up_adjusted(sorted_p, m_tests, 1.0)
        bhy_adj = _step_up_adjusted(sorted_p, m_tests, c_factor)

        for rank, f in enumerate(sorted_factors, start=1):
            f.p_value_rank = rank
            f.is_raw_significant = (f.raw_p_value <= self.alpha_target)
            f.is_hlz_t3_significant = (abs(f.raw_t_stat) >= self.hlz_t_threshold)

            f.adjusted_p_value_bonferroni = bonferroni_adj[rank - 1]
            f.adjusted_p_value_holm = holm_adj[rank - 1]
            f.adjusted_p_value_bh = bh_adj[rank - 1]
            f.adjusted_p_value_bhy = bhy_adj[rank - 1]

            # Comparing the adjusted p-value against the level reproduces each
            # procedure's own rejection rule exactly, so flags and adjusted p-values
            # can never disagree.
            f.is_bonferroni_significant = (f.adjusted_p_value_bonferroni <= self.alpha_target)
            f.is_holm_significant = (f.adjusted_p_value_holm <= self.alpha_target)
            f.is_bh_fdr_significant = (f.adjusted_p_value_bh <= self.fdr_q_target)
            f.is_bhy_fdr_significant = (f.adjusted_p_value_bhy <= self.fdr_q_target)

        raw_cnt = sum(1 for f in factors if f.is_raw_significant)
        bonf_cnt = sum(1 for f in factors if f.is_bonferroni_significant)
        holm_cnt = sum(1 for f in factors if f.is_holm_significant)
        bh_cnt = sum(1 for f in factors if f.is_bh_fdr_significant)
        bhy_cnt = sum(1 for f in factors if f.is_bhy_fdr_significant)
        hlz_cnt = sum(1 for f in factors if f.is_hlz_t3_significant)
        removed_cnt = max(0, raw_cnt - bh_cnt)

        notes = (
            f"MULTIPLE TESTING CORRECTION AUDIT (M={m_tests} tests, "
            f"{n_supplied} results supplied): "
            f"raw p<={self.alpha_target} = {raw_cnt}. "
            f"Bonferroni (FWER<={self.alpha_target}) = {bonf_cnt}, "
            f"Holm (FWER<={self.alpha_target}) = {holm_cnt}. "
            f"BH FDR (q*={self.fdr_q_target}) = {bh_cnt}; "
            f"BHY FDR (q*={self.fdr_q_target}, c(M)={c_factor:.4f}) = {bhy_cnt}. "
            f"Harvey-Liu-Zhu |t|>={self.hlz_t_threshold} = {hlz_cnt}. "
            f"{removed_cnt} raw-significant factor(s) not confirmed by BH "
            "(removed for lack of evidence, not proven false)."
        )
        logger.info(notes)

        return FactorMultipleTestingAuditReport(
            total_factors_tested=m_tests,
            alpha_target=self.alpha_target,
            fdr_q_target=self.fdr_q_target,
            raw_significant_count=raw_cnt,
            bonferroni_significant_count=bonf_cnt,
            holm_significant_count=holm_cnt,
            bh_fdr_significant_count=bh_cnt,
            hlz_t3_significant_count=hlz_cnt,
            false_discoveries_filtered_count=removed_cnt,
            audited_factors=sorted_factors,
            audit_notes=notes,
            bhy_fdr_significant_count=bhy_cnt,
            factor_results_supplied=n_supplied,
            bhy_dependence_factor=c_factor,
            hlz_t_threshold=self.hlz_t_threshold,
        )
