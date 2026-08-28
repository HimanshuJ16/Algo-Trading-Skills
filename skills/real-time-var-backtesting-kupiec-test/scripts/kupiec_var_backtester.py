"""
real-time-var-backtesting-kupiec-test: Kupiec Proportion-of-Failures (POF)
likelihood-ratio test plus Basel supervisory traffic-light zone classification.

Two distinct assessments are produced from the same input pair (T, x):

1. **Kupiec POF likelihood-ratio test** (Kupiec 1995, J. Derivatives 3(2) 73-84).
   Tests the *unconditional coverage* null H0: the true exception probability equals
   p = 1 - confidence_level.

       LR_POF = -2 * ln[ ((1-p)^(T-x) * p^x) / ((1-pi)^(T-x) * pi^x) ],  pi = x / T

   LR_POF is asymptotically chi-square with 1 degree of freedom, so the test is
   **two-sided**: it rejects both a model that breaches too often (understated risk)
   and one that breaches too rarely (overstated risk / wasted capital). The reported
   ``breach_direction`` says which side a rejection came from.

2. **Basel supervisory traffic-light zone** (BCBS, "Supervisory framework for the use
   of 'backtesting' in conjunction with the internal models approach to market risk
   capital requirements", January 1996 (bcbs22), Table 2; carried into the consolidated
   Basel Framework at MAR32.8-MAR32.15 and MAR99). This is a **one-sided** supervisory
   classification driven purely by the upper tail of the binomial distribution:

       amber zone begins at the smallest x with P(X <= x) >= 95%
       red zone   begins at the smallest x with P(X <= x) >= 99.99%

   At T = 250 and p = 0.01 this reproduces the published boundaries exactly: green
   0-4, amber 5-9, red 10 or more. MAR32.8 renamed the 1996 "yellow" zone to "amber";
   they are the same zone.

The two assessments are complementary, not interchangeable, and they legitimately
disagree. At T = 250, x = 6 the Kupiec test does not reject (p = 0.0594) while the
Basel classification is already amber; at T = 250, x = 0 Kupiec rejects (p = 0.0250,
model far too conservative) while Basel reports green, because the supervisory zones
only penalise excess breaches. Report both; never substitute one for the other.

Design notes:

- **No SciPy dependency.** The chi-square(1) survival function is exact in the standard
  library: P(chi2_1 > s) = erfc(sqrt(s / 2)). An earlier revision of this module
  approximated it as exp(-s / 2), which is the chi-square(**2**) survival function and
  overstated p-values by roughly 3x near the decision boundary (it returns 0.1465 at
  the 5% critical value 3.8415, where the correct answer is 0.0500). Binomial
  cumulative probabilities are accumulated in log space, reproducing BCBS Table 2 to
  the published two decimal places.
- **Deterministic and side-effect free.** Same inputs always produce the same result;
  no global state, no environment-dependent code path.

Limitations (documented, deliberate):

- **The POF test is only a test of unconditional coverage.** It counts breaches and is
  blind to their ordering, so a model whose breaches all arrive in one cluster passes
  as readily as one whose breaches are evenly spread. Clustering violates the
  independence property and requires a separate test (Christoffersen 1998 Markov test,
  or Christoffersen-Pelletier 2004 duration test).
- **Low power at regulatory sample sizes.** Kupiec (1995) reports that with the
  one-year threshold of 8 violations, a model reporting a 3% VaR while claiming 1% is
  detected only ~65% of the time (Campbell, FEDS 2005-21, Sec. 3.1). A non-rejection
  at T = 250 is weak evidence of adequacy, not a clean bill of health.
- **The chi-square distribution is asymptotic.** At small T, and especially at x = 0
  where the statistic sits on the boundary of the parameter space, the nominal p-value
  is unreliable. ``basel_cumulative_probability`` is exact in finite samples and should
  be preferred for small windows.
- **The Basel multiplier table is defined only for a 250-observation window at 99%
  coverage.** BCBS generalises the *zone boundaries* to other sample sizes via binomial
  probabilities but does not publish multiplier steps for them, so
  ``basel_backtesting_multiplier`` is None off that basis rather than extrapolated.
- **Exception counting is out of scope.** This module consumes an already-counted
  (T, x) pair. Under MAR32.18 the count is the greater of the actual-P&L and
  hypothetical-P&L exception counts; producing that count correctly is the caller's job.
"""
import logging
import math
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

#: Chi-square(1) critical value at the 5% level. erfc(sqrt(x/2)) == 0.05 at this point.
CHI2_1DF_CRITICAL_VALUE_5PCT = 3.841458820694124

#: BCBS requires backtesting over the most recent twelve months, "approximately 250
#: daily observations" (bcbs22 Sec. 2; MAR32.18 "at least one year").
BASEL_MINIMUM_OBSERVATIONS = 250

#: Sample size and coverage level the published Basel zone table is defined on.
BASEL_REFERENCE_SAMPLE_SIZE = 250
BASEL_REFERENCE_EXCEPTION_RATE = 0.01

#: Cumulative-probability rule BCBS gives for deducing zone boundaries at other sample
#: sizes (bcbs22 Table 2 notes): "the yellow zone begins at the point where the
#: cumulative probability equals or exceeds 95%, and the red zone begins at the point
#: where the cumulative probability equals or exceeds 99.99%."
BASEL_AMBER_ZONE_CUMULATIVE_PROBABILITY = 0.95
BASEL_RED_ZONE_CUMULATIVE_PROBABILITY = 0.9999

#: Backtesting-dependent multiplier by exception count, Basel Framework MAR32.9 Table 1
#: (in force). Defined for a 250-observation sample at 99% coverage only. 10 or more
#: exceptions maps to BASEL_RED_ZONE_MULTIPLIER.
MAR32_BACKTESTING_MULTIPLIER: Dict[int, float] = {
    0: 1.50, 1: 1.50, 2: 1.50, 3: 1.50, 4: 1.50,
    5: 1.70, 6: 1.76, 7: 1.83, 8: 1.88, 9: 1.92,
}
BASEL_RED_ZONE_MULTIPLIER = 2.00

ZONE_GREEN = "green"
ZONE_AMBER = "amber"
ZONE_RED = "red"

DIRECTION_UNDER_ESTIMATING = "under_estimating_risk"
DIRECTION_OVER_ESTIMATING = "over_estimating_risk"
DIRECTION_ALIGNED = "aligned"


@dataclass
class KupiecResult:
    """
    Outcome of one Kupiec POF backtest plus its Basel zone classification.

    ``p_value`` / ``is_rejected`` / ``stat`` describe the **two-sided** Kupiec test.
    ``basel_zone`` / ``basel_backtesting_multiplier`` describe the **one-sided**
    supervisory classification. They answer different questions and can disagree.
    """
    p_value: float
    is_rejected: bool
    exceptions: int
    stat: float = 0.0
    total_observations: int = 0
    expected_exception_rate: float = 0.0
    observed_exception_rate: float = 0.0
    expected_exceptions: float = 0.0
    breach_direction: str = DIRECTION_ALIGNED
    basel_zone: str = ZONE_GREEN
    basel_cumulative_probability: float = 0.0
    basel_backtesting_multiplier: Optional[float] = None
    meets_basel_minimum_observations: bool = False
    notes: str = ""


def binomial_cdf(k: int, n: int, p: float) -> float:
    """
    Exact P(X <= k) for X ~ Binomial(n, p), accumulated in log space.

    Reproduces BCBS bcbs22 Table 2 at n = 250, p = 0.01 to the published two decimals
    (k = 0 -> 8.11%, k = 4 -> 89.22%, k = 5 -> 95.88%, k = 9 -> 99.97%, k = 10 -> 99.99%).
    Log-space accumulation keeps the binomial coefficient from overflowing at the
    multi-thousand-observation windows a real-time backtester accumulates.
    """
    if n < 0:
        raise ValueError(f"n must be non-negative, got {n}.")
    if not 0.0 <= p <= 1.0:
        raise ValueError(f"p must lie in [0, 1], got {p}.")
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    if p == 0.0:
        return 1.0
    if p == 1.0:
        return 0.0

    log_p = math.log(p)
    log_q = math.log1p(-p)
    log_coefficient = 0.0
    total = 0.0
    for i in range(0, k + 1):
        if i > 0:
            log_coefficient += math.log(n - i + 1) - math.log(i)
        total += math.exp(log_coefficient + i * log_p + (n - i) * log_q)
    # Accumulated rounding can push the sum a hair past 1.0; a probability may not.
    return min(1.0, total)


def chi_square_1df_survival(statistic: float) -> float:
    """
    Exact upper-tail probability P(chi2_1 > statistic) = erfc(sqrt(statistic / 2)).

    This is the identity, not an approximation: a chi-square(1) variate is the square
    of a standard normal, so its survival function is 2 * (1 - Phi(sqrt(s))), which is
    exactly erfc(sqrt(s / 2)). Returns 0.05 at CHI2_1DF_CRITICAL_VALUE_5PCT.
    """
    if statistic < 0.0:
        raise ValueError(f"Chi-square statistic must be non-negative, got {statistic}.")
    return math.erfc(math.sqrt(statistic / 2.0))


def kupiec_pof_statistic(
    total_observations: int,
    exceptions: int,
    expected_exception_rate: float,
) -> float:
    """
    Kupiec (1995) proportion-of-failures likelihood-ratio statistic.

        LR_POF = -2 * [ (T-x)*ln(1-p) + x*ln(p) - (T-x)*ln(1-pi) - x*ln(pi) ],  pi = x/T

    Evaluated in log space so the p^x factor cannot underflow to zero at the small p
    and large T this test is run on (0.01 ** 25 is already ~1e-50).

    The x = 0 and x = T cases are the analytic limits of the expression: the
    unrestricted-likelihood terms x*ln(pi) and (T-x)*ln(1-pi) both tend to zero, so the
    statistic collapses to -2*T*ln(1-p) and -2*T*ln(p) respectively.

    Independently verified against published values in Campbell, "A Review of
    Backtesting and Backtesting Procedures", FEDS 2005-21, Sec. 3.1: T = 250, x = 4
    gives 0.769 (paper: 0.76) and T = 250, x = 10 gives 12.956 (paper: 12.95).
    """
    t = total_observations
    x = exceptions
    p = expected_exception_rate

    if x == 0:
        statistic = -2.0 * t * math.log1p(-p)
    elif x == t:
        statistic = -2.0 * t * math.log(p)
    else:
        pi_hat = x / t
        restricted = (t - x) * math.log1p(-p) + x * math.log(p)
        unrestricted = (t - x) * math.log1p(-pi_hat) + x * math.log(pi_hat)
        statistic = -2.0 * (restricted - unrestricted)

    # The unrestricted MLE maximises the likelihood by construction, so the statistic is
    # non-negative in exact arithmetic; clamp only the floating-point noise around
    # pi_hat == p, which would otherwise make sqrt() of a negative blow up downstream.
    return max(0.0, statistic)


def basel_zone_boundaries(
    total_observations: int,
    expected_exception_rate: float,
) -> Tuple[int, int]:
    """
    (amber_start, red_start) exception counts per the BCBS cumulative-probability rule.

    bcbs22 Table 2 notes: for sample sizes other than 250, the amber ("yellow") zone
    begins at the smallest exception count whose cumulative binomial probability is at
    least 95%, and the red zone at the smallest count whose cumulative probability is at
    least 99.99%. At T = 250, p = 0.01 this returns (5, 10), reproducing the published
    table.

    Note this is *not* the linear rescaling (x * 250 / T) sometimes used as a shortcut;
    the binomial tail is not linear in the sample size, and rescaling misclassifies at
    both small and large windows.
    """
    amber_start: Optional[int] = None
    red_start: Optional[int] = None
    for k in range(0, total_observations + 1):
        cumulative = binomial_cdf(k, total_observations, expected_exception_rate)
        if amber_start is None and cumulative >= BASEL_AMBER_ZONE_CUMULATIVE_PROBABILITY:
            amber_start = k
        if cumulative >= BASEL_RED_ZONE_CUMULATIVE_PROBABILITY:
            red_start = k
            break
    if amber_start is None:
        amber_start = total_observations
    if red_start is None:
        red_start = total_observations
    # On a degenerate window the raw rule can place a boundary at zero exceptions: at
    # T = 1, P(X <= 0) = 0.99 already clears the 95% threshold. The supervisory zones
    # penalise an *excess* of breaches, so a "zero breaches, amber" verdict is an
    # artifact of the sample size, not a finding. A zone can never begin below one
    # exception, and the red zone can never begin before the amber zone.
    amber_start = max(1, amber_start)
    red_start = max(amber_start, red_start)
    return amber_start, red_start


class KupiecVaRBacktester:
    """
    Kupiec POF backtester with Basel supervisory zone classification.

    Args:
        confidence_level: VaR coverage level being tested, e.g. 0.99 for 99% VaR. The
            expected exception rate is p = 1 - confidence_level.
        alpha: **statistical significance level** for rejecting the Kupiec null
            hypothesis (default 0.05). This is not the VaR confidence level -- passing
            0.99 here would reject essentially every model. Named ``alpha`` for
            backward compatibility with the original public API.
    """

    def __init__(self, confidence_level: float = 0.99, alpha: float = 0.05) -> None:
        if not 0.0 < confidence_level < 1.0:
            raise ValueError(
                f"confidence_level must lie strictly in (0, 1), got {confidence_level}. "
                "Pass the VaR coverage level, e.g. 0.99 for 99% VaR."
            )
        if not 0.0 < alpha < 1.0:
            raise ValueError(
                f"alpha (statistical significance level) must lie strictly in (0, 1), "
                f"got {alpha}. Typical value is 0.05."
            )
        if alpha >= 0.5:
            logger.warning(
                "alpha=%s is a significance level, not a VaR confidence level; a value "
                "this high rejects almost every model.", alpha
            )
        self.confidence_level = confidence_level
        self.alpha = alpha

    def run_test(self, total_observations: int, exceptions: int) -> KupiecResult:
        """
        Run the Kupiec POF test and classify the result into a Basel zone.

        Raises ValueError on an unusable input pair rather than returning a passing
        result. An empty or negative observation window is a broken data pipeline, and
        the earlier behaviour of returning "model accepted" for T <= 0 turned a feed
        outage into a silent all-clear on a regulatory control.
        """
        if isinstance(total_observations, bool) or not isinstance(total_observations, int):
            raise TypeError(
                f"total_observations must be an int, got {type(total_observations).__name__}."
            )
        if isinstance(exceptions, bool) or not isinstance(exceptions, int):
            raise TypeError(f"exceptions must be an int, got {type(exceptions).__name__}.")
        if total_observations < 1:
            raise ValueError(
                f"total_observations must be >= 1, got {total_observations}. An empty "
                "backtest window cannot validate or invalidate a VaR model."
            )
        if exceptions < 0:
            raise ValueError(f"exceptions must be >= 0, got {exceptions}.")
        if exceptions > total_observations:
            raise ValueError(
                f"exceptions ({exceptions}) cannot exceed total_observations "
                f"({total_observations})."
            )

        p = 1.0 - self.confidence_level
        statistic = kupiec_pof_statistic(total_observations, exceptions, p)
        p_value = chi_square_1df_survival(statistic)
        is_rejected = p_value < self.alpha

        observed_rate = exceptions / total_observations
        # Compared with a tolerance, not for exact equality: 1.0 - 0.99 is
        # 0.010000000000000009 in binary floating point, so a perfectly calibrated
        # 10-in-1000 result would otherwise be labelled as overstating risk.
        if math.isclose(observed_rate, p, rel_tol=1e-9, abs_tol=1e-15):
            direction = DIRECTION_ALIGNED
        elif observed_rate > p:
            direction = DIRECTION_UNDER_ESTIMATING
        else:
            direction = DIRECTION_OVER_ESTIMATING

        amber_start, red_start = basel_zone_boundaries(total_observations, p)
        if exceptions >= red_start:
            zone = ZONE_RED
        elif exceptions >= amber_start:
            zone = ZONE_AMBER
        else:
            zone = ZONE_GREEN

        cumulative = binomial_cdf(exceptions, total_observations, p)

        multiplier: Optional[float] = None
        on_published_basis = (
            total_observations == BASEL_REFERENCE_SAMPLE_SIZE
            and math.isclose(p, BASEL_REFERENCE_EXCEPTION_RATE, rel_tol=1e-12)
        )
        if on_published_basis:
            multiplier = MAR32_BACKTESTING_MULTIPLIER.get(exceptions, BASEL_RED_ZONE_MULTIPLIER)

        meets_minimum = total_observations >= BASEL_MINIMUM_OBSERVATIONS
        if not meets_minimum:
            logger.warning(
                "Backtest window of %d observations is below the Basel minimum of %d "
                "(approximately twelve months of daily data); the chi-square "
                "approximation and the test's power are both degraded.",
                total_observations, BASEL_MINIMUM_OBSERVATIONS,
            )

        note_parts = [
            f"Kupiec POF: T={total_observations}, x={exceptions}, "
            f"expected={total_observations * p:.2f}, LR={statistic:.4f}, "
            f"p={p_value:.6f}, rejected={is_rejected} (two-sided, alpha={self.alpha}).",
            f"Basel zone={zone} (amber from {amber_start}, red from {red_start}; "
            f"cumulative P(X<={exceptions})={cumulative:.6f}).",
        ]
        if multiplier is not None:
            note_parts.append(f"MAR32.9 backtesting multiplier={multiplier:.2f}.")
        else:
            note_parts.append(
                "MAR32.9 multiplier not applicable: the published table is defined only "
                f"for a {BASEL_REFERENCE_SAMPLE_SIZE}-observation window at 99% coverage."
            )
        if not meets_minimum:
            note_parts.append(
                f"WARNING: below the Basel {BASEL_MINIMUM_OBSERVATIONS}-observation minimum."
            )
        if is_rejected and direction == DIRECTION_OVER_ESTIMATING:
            note_parts.append(
                "Rejection is on the conservative side: too FEW breaches. The model "
                "overstates risk and over-consumes capital; it does not understate it."
            )

        return KupiecResult(
            p_value=p_value,
            is_rejected=is_rejected,
            exceptions=exceptions,
            stat=statistic,
            total_observations=total_observations,
            expected_exception_rate=p,
            observed_exception_rate=observed_rate,
            expected_exceptions=total_observations * p,
            breach_direction=direction,
            basel_zone=zone,
            basel_cumulative_probability=cumulative,
            basel_backtesting_multiplier=multiplier,
            meets_basel_minimum_observations=meets_minimum,
            notes=" ".join(note_parts),
        )
