"""Lower-tail dependence and exceedance-correlation analysis for multi-strategy portfolios.

Answers one question: do two strategies that look diversifying on the full sample
stay diversifying in the joint left tail?

Two statistics are computed on the aligned overlap of the pair:

* Quantile exceedance correlation

      rho_exc(alpha) = corr(R_A, R_B | R_A <= q_A(alpha) AND R_B <= q_B(alpha))

  the lower-tail exceedance correlation of Longin & Solnik, "Extreme Correlation
  of International Equity Markets", Journal of Finance 56(2), 2001, pp. 649-676,
  and Ang & Chen, "Asymmetric Correlations of Equity Portfolios", Journal of
  Financial Economics 63(3), 2002, pp. 443-494.

* Empirical tail dependence at level alpha

      chi_hat(alpha) = P(R_B <= q_B(alpha) | R_A <= q_A(alpha))

  a finite-alpha estimate of the chi(u) dependence function of Coles, Heffernan &
  Tawn, "Dependence Measures for Extreme Value Analyses", Extremes 2(4), 1999,
  pp. 339-365. It is NOT the copula coefficient lambda_L, which is the limit as
  u -> 0+; at alpha = 0.10 an independent pair scores 0.10, not 0.

Design decisions that matter for a tail-risk monitor
----------------------------------------------------

* Both statistics are compared against a Gaussian-copula benchmark, never against
  the unconditional correlation. Conditioning a sample on the size of its own
  variables changes the correlation of the retained subsample even when the true
  correlation is constant, so a raw "tail rho minus full-sample rho" gap measures
  the selection rule, not the strategies. See Boyer, Gibson & Loretan, "Pitfalls
  in Tests for Changes in Correlations", Federal Reserve IFDP No. 597, 1997, and
  Forbes & Rigobon, "No Contagion, Only Interdependence", Journal of Finance
  57(5), 2002, pp. 2223-2261. The benchmark here is simulated at the pair's own
  estimated rho, its own sample size and its own alpha, using the identical
  estimator, so the finite-sample selection bias cancels in the difference.

* The benchmark is a Gaussian copula because it is the "diversification holds in
  the tail" null: a bivariate normal with |rho| < 1 is asymptotically tail
  independent (lambda_L = 0) however high its correlation is. Excess over that
  benchmark is therefore the part of joint-tail comovement that a correlation
  number cannot explain. See Embrechts, McNeil & Straumann, "Correlation and
  Dependence in Risk Management: Properties and Pitfalls", in Risk Management:
  Value at Risk and Beyond, Cambridge University Press, 2002, pp. 176-223.

* An under-populated joint tail yields NaN and is_determinate=False, never a
  substituted number. The predecessor of this module fell back to the
  unconditional correlation, which forced the delta to zero and reported
  "diversification holds" precisely when there was no tail evidence at all. At
  alpha=0.10 an independent pair puts only about alpha^2 * n points in the joint
  tail -- roughly 5 for n=500 -- so this is the common case, not a corner case.

* Detection is one-sided. Negative tail comovement is a diversification benefit,
  not a breakdown.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Pearson correlation on 3 or fewer points is algebraically degenerate (+/-1 or
# undefined), so no configuration may take the joint-tail minimum below this.
MIN_TAIL_OBSERVATIONS_FLOOR = 5

# Below this many overlapping rows the alpha-quantile is a single order
# statistic and the joint tail cannot be populated at all.
MIN_OBSERVATIONS_FLOOR = 20

_STD_EPS = 1e-12


class TailCorrelationError(ValueError):
    """Raised when strategy return series are insufficient or malformed."""


@dataclass
class Config:
    """Configuration for tail correlation analysis.

    Thresholds are internal policy defaults, not standards -- no regulator or
    exchange mandates them. Calibrate them against the empirical distribution of
    your own strategies before wiring them to capital decisions.
    """

    enabled: bool = True
    tail_quantile: float = 0.10          # alpha: lower-tail stress threshold
    min_observations: int = 20           # minimum aligned overlapping rows
    min_tail_observations: int = 10      # minimum rows in the JOINT lower tail
    breakdown_threshold: float = 0.70    # absolute exceedance correlation level
    breakdown_excess_threshold: float = 0.20   # excess over Gaussian benchmark
    breakdown_max_pvalue: float = 0.05   # one-sided p-value against the benchmark
    benchmark_simulations: int = 1000    # Monte Carlo draws for the benchmark
    benchmark_seed: int = 12345          # fixed so results are reproducible

    def __post_init__(self) -> None:
        if not 0.0 < self.tail_quantile < 0.5:
            raise TailCorrelationError(
                f"tail_quantile must lie in (0, 0.5), got {self.tail_quantile!r}. "
                "It is a lower-tail probability; 0.10 and 0.05 are typical."
            )
        if self.min_observations < MIN_OBSERVATIONS_FLOOR:
            raise TailCorrelationError(
                f"min_observations must be >= {MIN_OBSERVATIONS_FLOOR}, "
                f"got {self.min_observations}."
            )
        if self.min_tail_observations < MIN_TAIL_OBSERVATIONS_FLOOR:
            raise TailCorrelationError(
                f"min_tail_observations must be >= {MIN_TAIL_OBSERVATIONS_FLOOR}, "
                f"got {self.min_tail_observations}."
            )
        if self.benchmark_simulations < 100:
            raise TailCorrelationError(
                f"benchmark_simulations must be >= 100, got {self.benchmark_simulations}."
            )
        if not 0.0 < self.breakdown_max_pvalue <= 1.0:
            raise TailCorrelationError(
                f"breakdown_max_pvalue must lie in (0, 1], got {self.breakdown_max_pvalue!r}."
            )


@dataclass
class TailCorrelationResult:
    """Outcome of a pairwise lower-tail analysis.

    ``lower_tail_correlation`` is the joint-tail exceedance correlation
    rho_exc(alpha); it is NaN when the joint tail is under-populated. Compare it
    with ``gaussian_benchmark_correlation``, not with
    ``unconditional_correlation`` -- ``tail_correlation_delta`` is reported for
    continuity only and is biased by the conditioning itself.
    """

    strategy_a: str
    strategy_b: str
    unconditional_correlation: float
    lower_tail_correlation: float
    tail_correlation_delta: float
    empirical_tail_dependence: float      # chi_hat(alpha) = P(B in tail | A in tail)
    joint_crash_probability: float        # P(A in tail AND B in tail)
    diversification_breakdown: bool
    details: List[str]
    # Gaussian-copula null, simulated at this pair's rho / n / alpha.
    gaussian_benchmark_correlation: float = float("nan")
    gaussian_benchmark_tail_dependence: float = float("nan")
    tail_correlation_excess: float = float("nan")
    tail_dependence_excess: float = float("nan")
    benchmark_pvalue: float = float("nan")
    # Independence reference for chi_hat(alpha): equals alpha exactly.
    independence_tail_dependence: float = float("nan")
    observations_used: int = 0
    joint_tail_observations: int = 0
    is_determinate: bool = False


def exceedance_correlation(
    a: np.ndarray,
    b: np.ndarray,
    tail_quantile: float,
    min_tail_observations: int,
) -> Tuple[float, int]:
    """Lower-tail quantile exceedance correlation and its joint-tail sample size.

    Conditions on the INTERSECTION of the two marginal lower tails, which is the
    Longin-Solnik / Ang-Chen definition and is symmetric in ``a`` and ``b``.
    Returns ``(nan, count)`` when the joint tail is too small or degenerate.
    """
    q_a = float(np.quantile(a, tail_quantile))
    q_b = float(np.quantile(b, tail_quantile))
    joint = (a <= q_a) & (b <= q_b)
    count = int(joint.sum())
    if count < min_tail_observations:
        return float("nan"), count
    tail_a, tail_b = a[joint], b[joint]
    if tail_a.std() <= _STD_EPS or tail_b.std() <= _STD_EPS:
        # A flat tail slice has an undefined correlation to anything.
        return float("nan"), count
    return float(np.corrcoef(tail_a, tail_b)[0, 1]), count


def empirical_tail_dependence(a: np.ndarray, b: np.ndarray, tail_quantile: float) -> float:
    """chi_hat(alpha) = P(B <= q_B(alpha) | A <= q_A(alpha)).

    Conditions on ``a``; the estimate is only exactly symmetric when both tails
    retain the same number of points, which ties can break. Under independence
    its expectation is ``tail_quantile`` itself, not zero.
    """
    q_a = float(np.quantile(a, tail_quantile))
    q_b = float(np.quantile(b, tail_quantile))
    tail_a = a <= q_a
    count_a = int(tail_a.sum())
    if count_a == 0:
        return float("nan")
    return float(((b <= q_b) & tail_a).sum()) / count_a


class TailCorrelationAnalyzerEngine:
    """Analyzes lower-tail dependence between strategy return series."""

    def __init__(self, config: Optional[Config] = None) -> None:
        self.config = config or Config()

    # ---------------------------------------------------------------- helpers

    def _validate_series(self, series: pd.Series, name: str) -> pd.Series:
        if not isinstance(series, pd.Series):
            raise TailCorrelationError(
                f"{name}: expected a pandas Series, got {type(series).__name__}."
            )
        if series.index.has_duplicates:
            raise TailCorrelationError(
                f"{name}: index contains duplicate labels; de-duplicate before aligning "
                "or the join will silently produce a cartesian product."
            )
        numeric = pd.to_numeric(series, errors="coerce")
        if int(numeric.isna().sum()) > int(series.isna().sum()):
            raise TailCorrelationError(f"{name}: series contains non-numeric values.")
        finite_values = numeric.dropna().to_numpy(dtype=float)
        if finite_values.size and not np.isfinite(finite_values).all():
            raise TailCorrelationError(
                f"{name}: series contains non-finite values (+/-inf). Clean the return "
                "series first -- an infinite return silently poisons every correlation."
            )
        return numeric.astype(float)

    def _gaussian_benchmark(
        self, rho: float, n_obs: int, observed: float
    ) -> Tuple[float, float, float]:
        """Simulate the Gaussian-copula null for the exceedance statistics.

        Returns ``(benchmark_correlation, benchmark_tail_dependence, p_value)``
        where the p-value is the one-sided fraction of null draws whose
        exceedance correlation is at least ``observed``.
        """
        cfg = self.config
        rho = float(np.clip(rho, -0.999, 0.999))
        rng = np.random.default_rng(cfg.benchmark_seed)
        # Cholesky of [[1, rho], [rho, 1]] applied to independent normals; the
        # statistics are computed in a vectorized sweep over simulations because
        # analyze_portfolio_matrix runs this once per pair.
        chol = np.array([[1.0, 0.0], [rho, float(np.sqrt(max(1.0 - rho * rho, 0.0)))]])
        chunk = max(1, min(cfg.benchmark_simulations, 4_000_000 // max(n_obs, 1)))

        corr_chunks: List[np.ndarray] = []
        chi_chunks: List[np.ndarray] = []
        remaining = cfg.benchmark_simulations
        while remaining > 0:
            size = min(chunk, remaining)
            remaining -= size
            z = rng.standard_normal((size, n_obs, 2)) @ chol.T
            x, y = z[:, :, 0], z[:, :, 1]

            q = np.quantile(z, cfg.tail_quantile, axis=1)           # (size, 2)
            in_a = x <= q[:, 0, None]
            in_b = y <= q[:, 1, None]
            joint = in_a & in_b
            counts = joint.sum(axis=1)

            # chi_hat(alpha) per simulation.
            count_a = in_a.sum(axis=1)
            with np.errstate(invalid="ignore", divide="ignore"):
                chi_chunks.append(np.where(count_a > 0, counts / np.maximum(count_a, 1), np.nan))

            # Pearson correlation restricted to the joint tail. The 1/n vs
            # 1/(n-1) normalization cancels in the ratio, so this equals
            # np.corrcoef on the masked slice.
            w = joint.astype(float)
            sw = w.sum(axis=1)
            safe_sw = np.maximum(sw, 1.0)
            mx = (w * x).sum(axis=1) / safe_sw
            my = (w * y).sum(axis=1) / safe_sw
            dx, dy = x - mx[:, None], y - my[:, None]
            cxy = (w * dx * dy).sum(axis=1)
            cxx = (w * dx * dx).sum(axis=1)
            cyy = (w * dy * dy).sum(axis=1)
            denom = np.sqrt(cxx * cyy)
            with np.errstate(invalid="ignore", divide="ignore"):
                c = np.where(denom > _STD_EPS, cxy / np.where(denom > 0, denom, 1.0), np.nan)
            corr_chunks.append(np.where(counts >= cfg.min_tail_observations, c, np.nan))

        corrs = np.concatenate(corr_chunks)
        chis = np.concatenate(chi_chunks)
        valid = corrs[~np.isnan(corrs)]
        if valid.size < 0.10 * cfg.benchmark_simulations:
            # The null itself cannot populate the joint tail at this n and alpha,
            # so no benchmark comparison is meaningful.
            return float("nan"), float(np.nanmean(chis)), float("nan")
        bench_corr = float(valid.mean())
        bench_chi = float(np.nanmean(chis))
        p_value = float("nan") if np.isnan(observed) else float((valid >= observed).mean())
        return bench_corr, bench_chi, p_value

    # ------------------------------------------------------------------- API

    def analyze_pair(
        self,
        returns_a: pd.Series,
        returns_b: pd.Series,
        strategy_a_name: str = "Strategy_A",
        strategy_b_name: str = "Strategy_B",
    ) -> TailCorrelationResult:
        """Compare a pair's joint lower-tail comovement against a Gaussian null.

        Raises ``TailCorrelationError`` on malformed, non-finite, mis-indexed or
        insufficient input. A pair whose joint tail is too thin to measure comes
        back with ``is_determinate=False`` and NaN tail statistics; it is never
        reported as diversifying.
        """
        cfg = self.config
        if strategy_a_name == strategy_b_name:
            raise TailCorrelationError(
                f"Both series are named {strategy_a_name!r}; distinct names are required."
            )
        series_a = self._validate_series(returns_a, strategy_a_name)
        series_b = self._validate_series(returns_b, strategy_b_name)

        # Align on index. Equal lengths do NOT imply a shared index, so alignment
        # happens before any sufficiency check.
        df = pd.DataFrame({strategy_a_name: series_a, strategy_b_name: series_b}).dropna()
        n_obs = len(df)
        if n_obs < cfg.min_observations:
            raise TailCorrelationError(
                f"Need >= {cfg.min_observations} aligned non-null overlapping observations "
                f"for {strategy_a_name}/{strategy_b_name}, got {n_obs}. Check that both "
                "series share a timestamp index."
            )
        dropped = max(len(series_a), len(series_b)) - n_obs
        if dropped > 0:
            logger.warning(
                "%s/%s: %d of %d rows dropped by alignment/NaN removal; tail statistics "
                "use %d overlapping observations.",
                strategy_a_name,
                strategy_b_name,
                dropped,
                max(len(series_a), len(series_b)),
                n_obs,
            )

        a = df[strategy_a_name].to_numpy(dtype=float)
        b = df[strategy_b_name].to_numpy(dtype=float)
        if a.std() <= _STD_EPS or b.std() <= _STD_EPS:
            raise TailCorrelationError(
                f"{strategy_a_name}/{strategy_b_name}: a series has zero variance over the "
                "overlap (flat, stale or idle). Its correlation is undefined; substituting "
                "0.0 would make a dead feed look perfectly diversifying."
            )

        uncond_corr = float(np.corrcoef(a, b)[0, 1])
        tail_corr, joint_count = exceedance_correlation(
            a, b, cfg.tail_quantile, cfg.min_tail_observations
        )
        chi_hat = empirical_tail_dependence(a, b, cfg.tail_quantile)
        joint_crash_prob = joint_count / n_obs

        bench_corr, bench_chi, p_value = self._gaussian_benchmark(uncond_corr, n_obs, tail_corr)
        corr_excess = tail_corr - bench_corr
        chi_excess = chi_hat - bench_chi
        is_determinate = not (np.isnan(tail_corr) or np.isnan(bench_corr))

        details: List[str] = []
        if not is_determinate:
            is_breakdown = False
            if np.isnan(tail_corr):
                cause = (
                    f"only {joint_count} observations fall in the joint lower "
                    f"{cfg.tail_quantile:.0%} tail (minimum {cfg.min_tail_observations})"
                    if joint_count < cfg.min_tail_observations
                    else f"the {joint_count}-observation joint tail is flat, so its "
                    "correlation is undefined"
                )
            else:
                # The sample cleared the bar but the null could not: at this n and
                # alpha too few simulated Gaussian draws populate a joint tail, so
                # there is nothing to compare the observed value against.
                cause = (
                    f"the Gaussian benchmark could not be estimated at n={n_obs} and "
                    f"alpha={cfg.tail_quantile:.2f} (the joint tail is too sparse under "
                    f"the null), leaving the observed {tail_corr:+.2f} uncalibrated"
                )
            details.append(
                f"INDETERMINATE: {cause}. No tail conclusion is supported -- do NOT read "
                "this as evidence of diversification. Extend the sample, raise "
                "tail_quantile, or add stress-scenario data."
            )
            logger.warning(
                "%s/%s: indeterminate tail analysis (joint tail n=%d, benchmark=%s).",
                strategy_a_name,
                strategy_b_name,
                joint_count,
                "unavailable" if np.isnan(bench_corr) else f"{bench_corr:.3f}",
            )
        else:
            is_breakdown = bool(
                (tail_corr >= cfg.breakdown_threshold)
                or (
                    corr_excess >= cfg.breakdown_excess_threshold
                    and not np.isnan(p_value)
                    and p_value <= cfg.breakdown_max_pvalue
                )
            )
            if is_breakdown:
                details.append(
                    f"DIVERSIFICATION BREAKDOWN WARNING: joint-tail exceedance correlation "
                    f"{tail_corr:+.2f} vs Gaussian benchmark {bench_corr:+.2f} "
                    f"(excess {corr_excess:+.2f}, one-sided p={p_value:.3f}); absolute "
                    f"threshold {cfg.breakdown_threshold:.2f}, excess threshold "
                    f"{cfg.breakdown_excess_threshold:.2f}."
                )
            else:
                details.append(
                    f"Tail diversification holds: exceedance correlation {tail_corr:+.2f} is "
                    f"within the Gaussian-copula benchmark {bench_corr:+.2f} "
                    f"(excess {corr_excess:+.2f}, one-sided p={p_value:.3f}), based on "
                    f"{joint_count} joint-tail observations."
                )
        details.append(
            f"chi_hat({cfg.tail_quantile:.2f})={chi_hat:.2f} vs Gaussian benchmark "
            f"{bench_chi:.2f} and independence baseline {cfg.tail_quantile:.2f}."
        )

        return TailCorrelationResult(
            strategy_a=strategy_a_name,
            strategy_b=strategy_b_name,
            unconditional_correlation=uncond_corr,
            lower_tail_correlation=tail_corr,
            tail_correlation_delta=tail_corr - uncond_corr,
            empirical_tail_dependence=chi_hat,
            joint_crash_probability=joint_crash_prob,
            diversification_breakdown=is_breakdown,
            details=details,
            gaussian_benchmark_correlation=bench_corr,
            gaussian_benchmark_tail_dependence=bench_chi,
            tail_correlation_excess=corr_excess,
            tail_dependence_excess=chi_excess,
            benchmark_pvalue=p_value,
            independence_tail_dependence=cfg.tail_quantile,
            observations_used=n_obs,
            joint_tail_observations=joint_count,
            is_determinate=is_determinate,
        )

    def analyze_portfolio_matrix(self, returns_df: pd.DataFrame) -> Dict[str, Any]:
        """Pairwise unconditional and joint-tail exceedance correlation matrices.

        Off-diagonal entries of ``lower_tail_matrix`` are NaN where the pair's
        joint tail was too thin to measure. Because every pair is estimated on
        its own overlap and its own tail subsample, neither matrix is guaranteed
        positive semi-definite -- do not feed them straight into an optimizer.
        """
        if not isinstance(returns_df, pd.DataFrame):
            raise TailCorrelationError(
                f"Expected a DataFrame, got {type(returns_df).__name__}."
            )
        strategies = list(returns_df.columns)
        if len(set(strategies)) != len(strategies):
            raise TailCorrelationError("Duplicate column names; strategy names must be unique.")
        if len(strategies) < 2:
            raise TailCorrelationError(
                f"Need at least 2 strategies to compare, got {len(strategies)}."
            )

        n = len(strategies)
        uncond_matrix = pd.DataFrame(np.eye(n), index=strategies, columns=strategies, dtype=float)
        tail_matrix = pd.DataFrame(np.eye(n), index=strategies, columns=strategies, dtype=float)
        breakdown_pairs: List[Tuple[str, str, float]] = []
        indeterminate_pairs: List[Tuple[str, str, int]] = []
        results: List[TailCorrelationResult] = []

        for i in range(n):
            for j in range(i + 1, n):
                s1, s2 = strategies[i], strategies[j]
                res = self.analyze_pair(returns_df[s1], returns_df[s2], str(s1), str(s2))
                results.append(res)
                uncond_matrix.loc[s1, s2] = res.unconditional_correlation
                uncond_matrix.loc[s2, s1] = res.unconditional_correlation
                tail_matrix.loc[s1, s2] = res.lower_tail_correlation
                tail_matrix.loc[s2, s1] = res.lower_tail_correlation
                if res.diversification_breakdown:
                    breakdown_pairs.append((s1, s2, res.lower_tail_correlation))
                elif not res.is_determinate:
                    indeterminate_pairs.append((s1, s2, res.joint_tail_observations))

        return {
            "unconditional_matrix": uncond_matrix,
            "lower_tail_matrix": tail_matrix,
            "breakdown_pairs": breakdown_pairs,
            "indeterminate_pairs": indeterminate_pairs,
            "results": results,
        }


class Engine:
    """Legacy Engine class wrapper."""

    def __init__(self, config: Optional[Config] = None) -> None:
        self.config = config or Config()
        self.analyzer = TailCorrelationAnalyzerEngine(self.config)

    def run(self) -> bool:
        return self.config.enabled
