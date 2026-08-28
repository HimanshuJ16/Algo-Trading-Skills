"""
risk-model-backtesting-against-realized-outcomes: backtest a forecast VaR (and,
optionally, Expected Shortfall) model against a *sequence of dated daily outcomes*.

This module is the **observation-level** layer of VaR model validation. It consumes the
raw daily record - date, realised P&L, forecast VaR, optionally hypothetical P&L and
forecast ES - and is responsible for turning that record into an exception count and an
auditable verdict. Three assessments are produced:

1. **Kupiec (1995) POF likelihood-ratio test** of *unconditional coverage*: is the
   exception rate consistent with p = 1 - confidence_level? Two-sided, chi-square(1).

2. **Christoffersen (1998) Markov test** of *independence*, plus the joint *conditional
   coverage* statistic LR_cc = LR_uc + LR_ind. Only an observation-level backtester can
   compute these: they read the ordering of the hit sequence, not just its total. A model
   whose breaches all arrive in one week passes Kupiec and fails here.

3. **Basel supervisory traffic-light zone** (BCBS "Supervisory framework for the use of
   'backtesting' ...", January 1996 (bcbs22), Table 2; carried into the Basel Framework
   at MAR32.8-MAR32.15). One-sided, driven purely by the upper binomial tail.

Sources verified against primary text:

- **Zone boundaries off a 250-day window.** bcbs22 Table 2 notes: "For other sample
  sizes, the yellow zone begins at the point where the cumulative probability equals or
  exceeds 95%, and the red zone begins at the point where the cumulative probability
  equals or exceeds 99.99%." An earlier revision of this module instead rescaled the
  exception count linearly (``x * 250 / N``). That is not the published rule and it fails
  in both directions: at N = 1000 it places the red boundary at 40 exceptions where the
  binomial rule places it at 24 (so a badly miscalibrated model is reported GREEN), and
  at N = 25 it turns a single exception into a RED-zone rejection.

- **Missing data counts as an exception.** MAR32.5(2) and MAR32.18(2): "In the event
  either the P&L or the daily VaR measure is not available or impossible to compute, it
  will count as an outlier." An earlier revision let a NaN P&L compare false and vanish
  from the count, so a broken feed reported as a clean backtest.

- **Actual vs hypothetical P&L.** MAR32.5(1): "exceptions for actual losses are counted
  separately from exceptions for hypothetical losses; the overall number of exceptions is
  the greater of these two amounts." Supply ``hypothetical_pnl_usd`` to apply this rule.

- **Red zone is not model disqualification.** bcbs22 Sec. III(f): the supervisor "should
  automatically increase the multiplication factor applicable to a firm's model by one
  (from three to four)"; MAR32.15 adds "or may disallow use of the model". The consequence
  is a capital multiplier, imposed by a supervisor - not an automatic disqualification.
  ``is_model_accepted`` reports whether the zone is free of a *presumption* of a flawed
  model; it is not a supervisory determination.

- **Expected Shortfall.** No supervisor prescribes an ES backtest; MAR32 backtests VaR
  even under an ES-based capital metric. When ``forecast_es_usd`` is supplied this module
  reports the Acerbi-Szekely (2014) Z2 statistic as a **signed diagnostic with no
  p-value**, because its critical value requires simulating the predictive distribution,
  which this module does not have.

Design notes:

- **No third-party dependencies.** The chi-square(1) survival function is exact in the
  standard library, P(chi2_1 > s) = erfc(sqrt(s/2)); chi-square(2) is exp(-s/2). Binomial
  cumulative probabilities are accumulated in log space and reproduce all eleven rows of
  bcbs22 Table 2 to the published two decimals.
- **Deterministic and side-effect free.** No global state, no environment-dependent path.
- **Full precision is returned.** Statistics are not rounded at the API boundary; a
  p-value of 1e-9 must not be reported as ``0.0`` in a regulatory audit trail.

Limitations (documented, deliberate):

- The Markov independence test only looks one day back. Breaches that cluster at a weekly
  lag violate independence but are invisible to it (Campbell, FEDS 2005-21, Sec. 3.2). The
  Christoffersen-Pelletier (2004) duration test has more power and is not implemented here.
- The chi-square distributions are asymptotic. Below the Basel 250-observation basis, and
  at x = 0 or a degenerate transition table where the statistic sits on the boundary of
  the parameter space, the nominal p-value is unreliable. The exact binomial cumulative
  probability is reported alongside and should be preferred on short windows.
- The capital multiplier tables are published only for a 250-observation window at 99%
  coverage. Off that basis they are reported as ``None`` rather than extrapolated.
- Exception *aggregation* across desks, and the non-modellable-risk-factor carve-out of
  MAR32.6, are out of scope.
"""
import logging
import math
from dataclasses import dataclass
from datetime import date as _date
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: Chi-square(1) critical value at the 5% level: erfc(sqrt(s/2)) == 0.05 here.
CHI2_1DF_CRITICAL_VALUE_5PCT = 3.841458820694124

#: Chi-square(2) critical value at the 5% level: exp(-s/2) == 0.05 here.
CHI2_2DF_CRITICAL_VALUE_5PCT = 5.991464547107979

#: Sample size and coverage the published Basel zone and multiplier tables are defined on.
#: bcbs22 Table 2; MAR32.3(3) "over the course of 12 months (ie 250 trading days)".
BASEL_REFERENCE_SAMPLE_SIZE = 250
BASEL_REFERENCE_EXCEPTION_RATE = 0.01

#: Cumulative-probability rule bcbs22 Table 2 gives for deducing the zone boundaries at
#: sample sizes other than 250. Quoted in the module docstring.
BASEL_YELLOW_ZONE_CUMULATIVE_PROBABILITY = 0.95
BASEL_RED_ZONE_CUMULATIVE_PROBABILITY = 0.9999

#: bcbs22 Table 2, "Increase in scaling factor" column. These are *increments* to a base
#: scaling factor of three, not total multipliers.
BCBS22_SCALING_FACTOR_INCREASE: Dict[int, float] = {
    0: 0.00, 1: 0.00, 2: 0.00, 3: 0.00, 4: 0.00,
    5: 0.40, 6: 0.50, 7: 0.65, 8: 0.75, 9: 0.85,
}
BCBS22_RED_ZONE_SCALING_FACTOR_INCREASE = 1.00

#: Basel Framework MAR32.9 Table 1, "Backtesting dependent multiplier". These are *total*
#: multipliers on a different base; do not add them to the bcbs22 increments above.
MAR32_BACKTESTING_MULTIPLIER: Dict[int, float] = {
    0: 1.50, 1: 1.50, 2: 1.50, 3: 1.50, 4: 1.50,
    5: 1.70, 6: 1.76, 7: 1.83, 8: 1.88, 9: 1.92,
}
MAR32_RED_ZONE_MULTIPLIER = 2.00

#: 17 CFR 240.15c3-1e Appendix E, Table 1, for SEC alternative-net-capital broker-dealers.
#: Total multiplication factors on a base of three; equals 3 + the bcbs22 increment.
SEC_APPENDIX_E_MULTIPLICATION_FACTOR: Dict[int, float] = {
    0: 3.00, 1: 3.00, 2: 3.00, 3: 3.00, 4: 3.00,
    5: 3.40, 6: 3.50, 7: 3.65, 8: 3.75, 9: 3.85,
}
SEC_APPENDIX_E_RED_ZONE_MULTIPLICATION_FACTOR = 4.00

#: Hard floor below which the asymptotic tests are not worth reporting at all. This is a
#: usability floor chosen by this module, not a regulatory threshold; the regulatory basis
#: is BASEL_REFERENCE_SAMPLE_SIZE.
MINIMUM_OBSERVATIONS = 20

PNL_BASIS_ACTUAL = "actual"
PNL_BASIS_HYPOTHETICAL = "hypothetical"


class BaselZone(str, Enum):
    """
    Supervisory traffic-light zone. MAR32.8 renamed the 1996 "yellow" zone to "amber";
    they are the same zone and the 1996 name is retained here for continuity.
    """
    GREEN = "GREEN"      # No presumption of a problem; no backtesting add-on.
    YELLOW = "YELLOW"    # Consistent with either an accurate or an inaccurate model.
    RED = "RED"          # Almost certainly indicates a problem with the model.


@dataclass
class Result:
    """Legacy Result container for backward compatibility."""
    success: bool
    message: str


@dataclass
class DailyRiskObservation:
    """
    One trading day of the backtest record.

    Args:
        date_iso: Calendar date in ISO 8601 form (YYYY-MM-DD). Parsed strictly and
            required to be unique and strictly increasing across the series, because the
            independence test reads the *ordering* of the hit sequence.
        realized_pnl_usd: Actual daily net trading P&L. Negative for a loss. This is the
            "actual P&L (APL)" of MAR32.4.
        forecast_var_usd: The one-day VaR forecast as a **positive** magnitude, e.g.
            100_000.0 for a 100k VaR limit. A non-positive value is rejected rather than
            silently absolute-valued: a zero or negative VaR is a broken forecast feed,
            and treating it as a limit would flag every losing day as an exception.
        confidence_level: VaR coverage this observation's forecast was produced at. Must
            match the engine's own confidence level; a mismatch is rejected rather than
            silently ignored, because mixing 95% and 99% forecasts in one window makes the
            exception count meaningless.
        hypothetical_pnl_usd: Optional "hypothetical P&L (HPL)" - the change in portfolio
            value had end-of-day positions been held unchanged (bcbs22 Sec. II; MAR32.4).
            When supplied for every observation, MAR32.5(1) applies and the governing
            exception count is the greater of the actual and hypothetical counts.
        forecast_es_usd: Optional one-day Expected Shortfall / CVaR forecast, as a positive
            magnitude, at the same coverage level. Enables the Acerbi-Szekely Z2 diagnostic.
    """
    date_iso: str
    realized_pnl_usd: float
    forecast_var_usd: float
    confidence_level: float = 0.99
    hypothetical_pnl_usd: Optional[float] = None
    forecast_es_usd: Optional[float] = None


@dataclass
class VaRException:
    """
    One backtesting exception.

    ``breach_amount_usd`` is the amount by which the loss exceeded the VaR forecast; it is
    ``None`` for an exception raised under MAR32.5(2) because the P&L or the VaR measure
    was unavailable, where no magnitude exists.
    """
    date_iso: str
    realized_pnl_usd: float
    forecast_var_usd: float
    breach_amount_usd: Optional[float]
    pnl_basis: str = PNL_BASIS_ACTUAL
    is_missing_data_outlier: bool = False


@dataclass
class RiskModelBacktestReport:
    """
    Outcome of one observation-level VaR backtest.

    ``kupiec_*`` describe the two-sided unconditional-coverage test. ``christoffersen_*``
    describe the independence and joint conditional-coverage tests. ``basel_zone`` and the
    multiplier fields describe the one-sided supervisory classification. They answer
    different questions and legitimately disagree - report all of them.
    """
    total_observations: int
    expected_exceptions: float
    actual_exceptions: int
    exception_rate_pct: float
    kupiec_lr_stat: float
    kupiec_p_value: float
    basel_zone: BaselZone
    exceptions: List[VaRException]
    is_model_accepted: bool
    audit_notes: str
    # --- appended in 2.0.0; all defaulted so positional construction stays valid ---
    christoffersen_ind_lr_stat: float = 0.0
    christoffersen_ind_p_value: float = 1.0
    christoffersen_cc_lr_stat: float = 0.0
    christoffersen_cc_p_value: float = 1.0
    exceptions_are_clustered: bool = False
    basel_yellow_zone_starts_at: int = 0
    basel_red_zone_starts_at: int = 0
    basel_cumulative_probability: float = 0.0
    bcbs22_scaling_factor_increase: Optional[float] = None
    mar32_backtesting_multiplier: Optional[float] = None
    sec_appendix_e_multiplication_factor: Optional[float] = None
    meets_basel_reference_sample_size: bool = False
    governing_pnl_basis: str = PNL_BASIS_ACTUAL
    actual_pnl_exceptions: int = 0
    hypothetical_pnl_exceptions: Optional[int] = None
    missing_data_outliers: int = 0
    es_acerbi_szekely_z2: Optional[float] = None
    es_underestimated: Optional[bool] = None


def binomial_cdf(k: int, n: int, p: float) -> float:
    """
    Exact P(X <= k) for X ~ Binomial(n, p), accumulated in log space.

    Reproduces all eleven rows of bcbs22 Table 2 at n = 250, p = 0.01 to the published two
    decimals (8.11%, 28.58%, 54.32%, 75.81%, 89.22%, 95.88%, 98.63%, 99.60%, 99.89%,
    99.97%, 99.99%). Log-space accumulation keeps the binomial coefficient from overflowing
    on the multi-thousand-observation windows a long-running backtest accumulates.
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
    Exact upper-tail probability P(chi2_1 > s) = erfc(sqrt(s / 2)).

    This is an identity, not an approximation: a chi-square(1) variate is the square of a
    standard normal, so its survival function is 2 * (1 - Phi(sqrt(s))) = erfc(sqrt(s/2)).
    Returns 0.05 at CHI2_1DF_CRITICAL_VALUE_5PCT. Note that exp(-s/2) is the chi-square(2)
    survival function and returns 0.1465 there - substituting it inflates p-values roughly
    threefold near the decision boundary and lets miscalibrated models pass.
    """
    if statistic < 0.0:
        raise ValueError(f"Chi-square statistic must be non-negative, got {statistic}.")
    return math.erfc(math.sqrt(statistic / 2.0))


def chi_square_2df_survival(statistic: float) -> float:
    """
    Exact upper-tail probability P(chi2_2 > s) = exp(-s / 2).

    Used for the joint conditional-coverage statistic, which has two degrees of freedom.
    Returns 0.05 at CHI2_2DF_CRITICAL_VALUE_5PCT.
    """
    if statistic < 0.0:
        raise ValueError(f"Chi-square statistic must be non-negative, got {statistic}.")
    return math.exp(-statistic / 2.0)


def kupiec_pof_statistic(total_observations: int, exceptions: int,
                         expected_exception_rate: float) -> float:
    """
    Kupiec (1995) proportion-of-failures likelihood-ratio statistic, in log space.

        LR_POF = -2 * [ (T-x)*ln(1-p) + x*ln(p) - (T-x)*ln(1-pi) - x*ln(pi) ],  pi = x/T

    The x = 0 and x = T cases are the analytic limits, -2*T*ln(1-p) and -2*T*ln(p): the
    unrestricted terms x*ln(pi) and (T-x)*ln(1-pi) both tend to zero. Evaluated in logs
    because p^x underflows (0.01 ** 25 is already ~1e-50).

    Asymptotically chi-square(1), so the test is **two-sided**: it rejects a model that
    breaches too often *and* one that breaches too rarely.
    """
    t, x, p = total_observations, exceptions, expected_exception_rate
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
    # non-negative in exact arithmetic; clamp only floating-point noise around pi_hat == p.
    return max(0.0, statistic)


def christoffersen_independence_statistic(hit_sequence: Sequence[int]) -> float:
    """
    Christoffersen (1998) Markov test of the independence property, in log space.

        LR_ind = -2 * ln[ (1-pi)^(n00+n10) * pi^(n01+n11)
                          / ( (1-pi01)^n00 * pi01^n01 * (1-pi11)^n10 * pi11^n11 ) ]

    where n_ij counts transitions from state i on day t-1 to state j on day t over the
    T-1 adjacent pairs, pi01 = n01/(n00+n01), pi11 = n11/(n10+n11), and
    pi = (n01+n11)/(n00+n01+n10+n11). Asymptotically chi-square(1).

    H0 is that a breach today is independent of whether one occurred yesterday. The
    statistic is zero when pi01 == pi11 and grows as breaches cluster. Kupiec's POF test is
    a function of the exception *count* alone and cannot see this: ten breaches arriving in
    one week and ten spread evenly give an identical POF statistic (Campbell, FEDS 2005-21,
    Sec. 3.2). Clustered breaches signal a model that is slow to react to changing market
    conditions, and a run of consecutive large losses can be harder to recover from than
    the same number spread out.

    Degenerate transition tables return 0.0. When there are no breaches at all, or no two
    adjacent days differ in state, the Markov model is unidentified and there is no
    evidence of dependence to report; returning 0.0 says "no evidence of clustering",
    which is the correct reading of an empty contingency cell.
    """
    n = len(hit_sequence)
    if n < 2:
        return 0.0

    n00 = n01 = n10 = n11 = 0
    for previous, current in zip(hit_sequence, hit_sequence[1:]):
        if previous == 0 and current == 0:
            n00 += 1
        elif previous == 0 and current == 1:
            n01 += 1
        elif previous == 1 and current == 0:
            n10 += 1
        else:
            n11 += 1

    total = n00 + n01 + n10 + n11
    breaches = n01 + n11
    # No breaches, or every transition is a breach: the restricted and unrestricted
    # likelihoods coincide and the Markov transition probabilities are unidentified.
    if breaches == 0 or breaches == total:
        return 0.0
    # A state that never occurs on day t-1 leaves one row of the contingency table empty,
    # so pi01 or pi11 is unestimable and there is no independence evidence to extract.
    if (n00 + n01) == 0 or (n10 + n11) == 0:
        return 0.0

    pi = breaches / total
    pi01 = n01 / (n00 + n01)
    pi11 = n11 / (n10 + n11)

    def _xlog(count: int, probability: float) -> float:
        # 0 * ln(0) is the analytic limit 0; guard it rather than raising on log(0).
        if count == 0:
            return 0.0
        return count * math.log(probability)

    restricted = _xlog(n00 + n10, 1.0 - pi) + _xlog(n01 + n11, pi)
    unrestricted = (
        _xlog(n00, 1.0 - pi01) + _xlog(n01, pi01)
        + _xlog(n10, 1.0 - pi11) + _xlog(n11, pi11)
    )
    return max(0.0, -2.0 * (restricted - unrestricted))


def basel_zone_boundaries(total_observations: int,
                          expected_exception_rate: float) -> Tuple[int, int]:
    """
    (yellow_start, red_start) exception counts per the bcbs22 cumulative-probability rule.

    bcbs22 Table 2 notes: "For other sample sizes, the yellow zone begins at the point
    where the cumulative probability equals or exceeds 95%, and the red zone begins at the
    point where the cumulative probability equals or exceeds 99.99%." At T = 250, p = 0.01
    this returns (5, 10), reproducing the published table.

    This is deliberately *not* the linear rescaling x * 250 / T. The binomial tail is not
    linear in sample size: at T = 1000 the correct boundaries are (15, 24), where rescaling
    would imply (20, 40) and report a model with 30 exceptions as green.
    """
    yellow_start: Optional[int] = None
    red_start: Optional[int] = None
    for k in range(0, total_observations + 1):
        cumulative = binomial_cdf(k, total_observations, expected_exception_rate)
        if yellow_start is None and cumulative >= BASEL_YELLOW_ZONE_CUMULATIVE_PROBABILITY:
            yellow_start = k
        if cumulative >= BASEL_RED_ZONE_CUMULATIVE_PROBABILITY:
            red_start = k
            break
    if yellow_start is None:
        yellow_start = total_observations
    if red_start is None:
        red_start = total_observations
    # On a very short window the raw rule can place a boundary at zero exceptions: at
    # T = 5, p = 0.01, P(X <= 0) = 0.951 already clears 95%. The zones penalise an *excess*
    # of breaches, so "zero breaches, yellow" is an artifact of the sample size, not a
    # finding. A zone can never begin below one exception, and red never before yellow.
    yellow_start = max(1, yellow_start)
    red_start = max(yellow_start, red_start)
    return yellow_start, red_start


def acerbi_szekely_z2(
    pnl: Sequence[float],
    forecast_var: Sequence[float],
    forecast_es: Sequence[float],
    expected_exception_rate: float,
) -> float:
    """
    Acerbi and Szekely (2014) Z2 Expected Shortfall backtest statistic.

        Z2(e, v, x) = x * 1{x + v < 0} / (alpha * e) + 1,   Z2_bar = mean over the window

    with x the realised P&L (negative for a loss), v the VaR forecast and e the ES forecast
    as positive magnitudes, and alpha = 1 - confidence_level. Under the null that the
    forecasts are correct, E[Z2_bar] = 0. Z2 is strictly increasing in both v and e, so
    **Z2_bar < 0 indicates that VaR and/or ES underestimate the realised risk**, and
    Z2_bar > 0 indicates over-conservatism (a window with no breaches at all returns
    exactly 1.0).

    **No p-value is returned.** The critical value of Z2_bar depends on the predictive
    distribution of the P&L, which must be simulated; this module has only the realised
    outcomes and the two forecast numbers, so it reports the signed statistic and leaves
    significance to the caller. Published fixed thresholds exist only for specific
    (alpha, significance) pairs and must not be reused at another alpha.
    """
    if not (len(pnl) == len(forecast_var) == len(forecast_es)):
        raise ValueError("pnl, forecast_var and forecast_es must be the same length.")
    if not pnl:
        raise ValueError("Cannot compute the Z2 statistic on an empty window.")
    if not 0.0 < expected_exception_rate < 1.0:
        raise ValueError(
            f"expected_exception_rate must lie strictly in (0, 1), "
            f"got {expected_exception_rate}."
        )

    total = 0.0
    for x, v, e in zip(pnl, forecast_var, forecast_es):
        if e <= 0.0:
            raise ValueError(f"Forecast ES must be a positive magnitude, got {e}.")
        indicator = 1.0 if (x + v) < 0.0 else 0.0
        total += (x * indicator) / (expected_exception_rate * e) + 1.0
    return total / len(pnl)


class RiskModelBacktesterEngine:
    """
    Observation-level VaR/ES model backtester.

    Consumes a dated series of daily outcomes and emits a ``RiskModelBacktestReport``
    carrying the Kupiec unconditional-coverage test, the Christoffersen independence and
    conditional-coverage tests, the Basel supervisory zone with its published capital
    multipliers, and - when ES forecasts are supplied - the Acerbi-Szekely Z2 diagnostic.

    Args:
        confidence_level: VaR coverage level under test, e.g. 0.99 for 99% VaR. The
            expected exception rate is p = 1 - confidence_level. Bank-wide backtesting
            under MAR32.5 is at the 99th percentile; MAR32.18 adds 97.5% at desk level.
        significance_level: **statistical significance level** for rejecting the Kupiec and
            Christoffersen nulls (default 0.05). This is not the VaR confidence level -
            passing 0.99 here rejects essentially every model.
    """

    def __init__(self, confidence_level: float = 0.99,
                 significance_level: float = 0.05) -> None:
        if not isinstance(confidence_level, (int, float)) or isinstance(confidence_level, bool):
            raise TypeError(
                f"confidence_level must be a number, got {type(confidence_level).__name__}."
            )
        if not math.isfinite(confidence_level) or not 0.0 < confidence_level < 1.0:
            raise ValueError(
                f"confidence_level must lie strictly in (0, 1), got {confidence_level}. "
                "Pass the VaR coverage level, e.g. 0.99 for 99% VaR."
            )
        if not math.isfinite(significance_level) or not 0.0 < significance_level < 1.0:
            raise ValueError(
                f"significance_level must lie strictly in (0, 1), got "
                f"{significance_level}. Typical value is 0.05."
            )
        self.confidence_level = float(confidence_level)
        self.significance_level = float(significance_level)
        self.alpha = 1.0 - self.confidence_level  # e.g. 0.01 for 99% VaR

    def execute(self, param: bool) -> Result:
        """Legacy execute method retained for backward compatibility."""
        if param:
            return Result(True, "Success")
        return Result(False, "Failure")

    # ------------------------------------------------------------------ validation ---

    def _validate(self, observations: Sequence[DailyRiskObservation]) -> None:
        """
        Reject an unusable window rather than returning a passing verdict on it.

        A malformed or out-of-order series is a data-pipeline failure. Returning GREEN for
        one turns a feed outage into a silent all-clear on a regulatory control, and the
        independence test is meaningless on a series that is not in chronological order.
        """
        if isinstance(observations, (str, bytes)) or not isinstance(observations, Sequence):
            raise TypeError(
                f"observations must be a sequence of DailyRiskObservation, got "
                f"{type(observations).__name__}."
            )
        n = len(observations)
        if n < MINIMUM_OBSERVATIONS:
            raise ValueError(
                f"Minimum {MINIMUM_OBSERVATIONS} daily observations required for VaR "
                f"backtesting, got {n}."
            )

        previous_date: Optional[_date] = None
        for index, obs in enumerate(observations):
            if not isinstance(obs, DailyRiskObservation):
                raise TypeError(
                    f"observations[{index}] must be a DailyRiskObservation, got "
                    f"{type(obs).__name__}."
                )
            try:
                parsed = _date.fromisoformat(obs.date_iso)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"observations[{index}].date_iso must be an ISO 8601 date "
                    f"(YYYY-MM-DD), got {obs.date_iso!r}: {exc}"
                ) from exc
            if previous_date is not None and parsed <= previous_date:
                raise ValueError(
                    f"observations must be in strictly increasing date order; "
                    f"observations[{index}].date_iso ({obs.date_iso}) does not follow "
                    f"{previous_date.isoformat()}. The independence test reads the "
                    "ordering of the hit sequence, and a duplicated date would "
                    "double-count an exception."
                )
            previous_date = parsed

            if not math.isclose(obs.confidence_level, self.confidence_level,
                                rel_tol=1e-9, abs_tol=1e-12):
                raise ValueError(
                    f"observations[{index}].confidence_level ({obs.confidence_level}) "
                    f"does not match the engine confidence_level "
                    f"({self.confidence_level}). Mixing coverage levels in one window "
                    "makes the exception count uninterpretable."
                )
            # A wrong *type* is a caller bug and must surface as one. A non-finite *value*
            # is missing data and is handled downstream as an outlier under MAR32.5(2), so
            # NaN and inf are deliberately allowed through here.
            for attribute in ("realized_pnl_usd", "forecast_var_usd",
                              "hypothetical_pnl_usd", "forecast_es_usd"):
                value = getattr(obs, attribute)
                if value is None and attribute in ("hypothetical_pnl_usd",
                                                   "forecast_es_usd"):
                    continue
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    raise TypeError(
                        f"observations[{index}].{attribute} must be a number, got "
                        f"{type(value).__name__} ({value!r})."
                    )
            # A non-positive VaR is a broken forecast, not a limit of zero: treating it as
            # a limit would flag every single losing day as an exception.
            if math.isfinite(obs.forecast_var_usd) and obs.forecast_var_usd <= 0.0:
                raise ValueError(
                    f"observations[{index}].forecast_var_usd must be a positive magnitude "
                    f"(e.g. 100000.0 for a 100k VaR limit), got {obs.forecast_var_usd}."
                )
            if obs.forecast_es_usd is not None:
                if math.isfinite(obs.forecast_es_usd) and obs.forecast_es_usd <= 0.0:
                    raise ValueError(
                        f"observations[{index}].forecast_es_usd must be a positive "
                        f"magnitude, got {obs.forecast_es_usd}."
                    )
                if (math.isfinite(obs.forecast_es_usd)
                        and math.isfinite(obs.forecast_var_usd)
                        and obs.forecast_es_usd < obs.forecast_var_usd):
                    logger.warning(
                        "observations[%d]: forecast ES (%s) is below forecast VaR (%s). "
                        "Expected Shortfall is the mean loss in the tail beyond VaR and "
                        "cannot be smaller than VaR for a coherent model; check the sign "
                        "and coverage conventions of the ES feed.",
                        index, obs.forecast_es_usd, obs.forecast_var_usd,
                    )

    # ------------------------------------------------------------- hit-sequence ---

    @staticmethod
    def _build_hit_sequence(
        observations: Sequence[DailyRiskObservation],
        basis: str,
    ) -> Tuple[List[int], List[VaRException]]:
        """
        Build the 0/1 hit sequence and the exception records for one P&L basis.

        Exception rule: realised P&L strictly below the negated VaR forecast, i.e.
        ``pnl < -var``. Additionally, per MAR32.5(2) and MAR32.18(2), a day on which either
        the P&L or the VaR measure is unavailable "will count as an outlier" - so a NaN or
        infinite value on either side is an exception, not a skipped day.
        """
        hits: List[int] = []
        exceptions: List[VaRException] = []
        for obs in observations:
            pnl = (obs.realized_pnl_usd if basis == PNL_BASIS_ACTUAL
                   else obs.hypothetical_pnl_usd)
            var = obs.forecast_var_usd
            unavailable = (
                pnl is None
                or not isinstance(pnl, (int, float))
                or isinstance(pnl, bool)
                or not math.isfinite(pnl)
                or not math.isfinite(var)
            )
            if unavailable:
                hits.append(1)
                exceptions.append(VaRException(
                    date_iso=obs.date_iso,
                    realized_pnl_usd=float("nan") if pnl is None else float(pnl),
                    forecast_var_usd=var,
                    breach_amount_usd=None,
                    pnl_basis=basis,
                    is_missing_data_outlier=True,
                ))
                continue
            if pnl < -var:
                hits.append(1)
                exceptions.append(VaRException(
                    date_iso=obs.date_iso,
                    realized_pnl_usd=float(pnl),
                    forecast_var_usd=var,
                    breach_amount_usd=round(-float(pnl) - var, 2),
                    pnl_basis=basis,
                    is_missing_data_outlier=False,
                ))
            else:
                hits.append(0)
        return hits, exceptions

    # -------------------------------------------------------------------- public ---

    def backtest_var_model(
        self,
        observations: Sequence[DailyRiskObservation],
    ) -> RiskModelBacktestReport:
        """
        Backtest a VaR model against a dated series of realised daily outcomes.

        Counts exceptions, runs the Kupiec POF and Christoffersen independence /
        conditional-coverage tests, assigns the Basel supervisory zone from the published
        binomial rule, and attaches the capital multipliers when the window sits on the
        published 250-observation, 99%-coverage basis.

        When ``hypothetical_pnl_usd`` is present on **every** observation, MAR32.5(1)
        applies: the actual and hypothetical exception counts are computed separately and
        the greater of the two governs the report. When it is present on only some
        observations the partial series is ignored, with a warning, rather than being
        silently mixed with the actual series.

        Raises:
            TypeError: the observation sequence or a field has the wrong type.
            ValueError: fewer than ``MINIMUM_OBSERVATIONS`` days, a malformed or
                out-of-order date, a non-positive VaR or ES forecast, or an observation
                whose ``confidence_level`` disagrees with the engine's.
        """
        self._validate(observations)
        n = len(observations)
        p = self.alpha

        actual_hits, actual_exceptions = self._build_hit_sequence(
            observations, PNL_BASIS_ACTUAL)

        hypothetical_count: Optional[int] = None
        hits, exceptions, basis = actual_hits, actual_exceptions, PNL_BASIS_ACTUAL
        supplied = sum(1 for o in observations if o.hypothetical_pnl_usd is not None)
        if supplied == n:
            hypothetical_hits, hypothetical_exceptions = self._build_hit_sequence(
                observations, PNL_BASIS_HYPOTHETICAL)
            hypothetical_count = sum(hypothetical_hits)
            # MAR32.5(1): "the overall number of exceptions is the greater of these two
            # amounts". Ties keep the actual-P&L series, which is the default basis.
            if hypothetical_count > len(actual_exceptions):
                hits, exceptions, basis = (
                    hypothetical_hits, hypothetical_exceptions, PNL_BASIS_HYPOTHETICAL)
        elif supplied > 0:
            logger.warning(
                "hypothetical_pnl_usd supplied on only %d of %d observations; the "
                "MAR32.5(1) 'greater of actual and hypothetical' rule needs a complete "
                "series and has been skipped. Backtesting on actual P&L alone.",
                supplied, n,
            )

        x = sum(hits)
        missing_data_outliers = sum(1 for e in exceptions if e.is_missing_data_outlier)
        expected_exceptions = n * p
        exception_rate_pct = (x / n) * 100.0

        kupiec_stat = kupiec_pof_statistic(n, x, p)
        kupiec_p = chi_square_1df_survival(kupiec_stat)

        ind_stat = christoffersen_independence_statistic(hits)
        ind_p = chi_square_1df_survival(ind_stat)
        # Christoffersen's decomposition: the joint conditional-coverage statistic is the
        # sum of the two components, on two degrees of freedom. The independence component
        # conditions on the first observation, which is asymptotically immaterial.
        cc_stat = kupiec_stat + ind_stat
        cc_p = chi_square_2df_survival(cc_stat)
        clustered = ind_p < self.significance_level

        yellow_start, red_start = basel_zone_boundaries(n, p)
        if x >= red_start:
            zone = BaselZone.RED
            is_accepted = False
        elif x >= yellow_start:
            zone = BaselZone.YELLOW
            is_accepted = True  # Yellow: capital add-on, but no presumption of a flaw.
        else:
            zone = BaselZone.GREEN
            is_accepted = True
        cumulative = binomial_cdf(x, n, p)

        on_published_basis = (
            n == BASEL_REFERENCE_SAMPLE_SIZE
            and math.isclose(p, BASEL_REFERENCE_EXCEPTION_RATE, rel_tol=1e-9)
        )
        bcbs22_increase: Optional[float] = None
        mar32_multiplier: Optional[float] = None
        sec_factor: Optional[float] = None
        if on_published_basis:
            bcbs22_increase = BCBS22_SCALING_FACTOR_INCREASE.get(
                x, BCBS22_RED_ZONE_SCALING_FACTOR_INCREASE)
            mar32_multiplier = MAR32_BACKTESTING_MULTIPLIER.get(
                x, MAR32_RED_ZONE_MULTIPLIER)
            sec_factor = SEC_APPENDIX_E_MULTIPLICATION_FACTOR.get(
                x, SEC_APPENDIX_E_RED_ZONE_MULTIPLICATION_FACTOR)

        z2, es_underestimated = self._expected_shortfall_diagnostic(
            observations, missing_data_outliers)

        notes = self._build_audit_notes(
            n=n, x=x, expected=expected_exceptions, rate_pct=exception_rate_pct,
            kupiec_stat=kupiec_stat, kupiec_p=kupiec_p, ind_stat=ind_stat, ind_p=ind_p,
            cc_stat=cc_stat, cc_p=cc_p, zone=zone, yellow_start=yellow_start,
            red_start=red_start, cumulative=cumulative, basis=basis,
            actual_count=len(actual_exceptions),
            hypothetical_count=hypothetical_count,
            missing_data_outliers=missing_data_outliers,
            on_published_basis=on_published_basis, mar32_multiplier=mar32_multiplier,
            bcbs22_increase=bcbs22_increase, sec_factor=sec_factor,
            z2=z2, clustered=clustered,
        )
        if zone == BaselZone.RED:
            logger.error(notes)
        elif zone == BaselZone.YELLOW:
            logger.warning(notes)
        else:
            logger.info(notes)

        return RiskModelBacktestReport(
            total_observations=n,
            expected_exceptions=expected_exceptions,
            actual_exceptions=x,
            exception_rate_pct=exception_rate_pct,
            kupiec_lr_stat=kupiec_stat,
            kupiec_p_value=kupiec_p,
            basel_zone=zone,
            exceptions=exceptions,
            is_model_accepted=is_accepted,
            audit_notes=notes,
            christoffersen_ind_lr_stat=ind_stat,
            christoffersen_ind_p_value=ind_p,
            christoffersen_cc_lr_stat=cc_stat,
            christoffersen_cc_p_value=cc_p,
            exceptions_are_clustered=clustered,
            basel_yellow_zone_starts_at=yellow_start,
            basel_red_zone_starts_at=red_start,
            basel_cumulative_probability=cumulative,
            bcbs22_scaling_factor_increase=bcbs22_increase,
            mar32_backtesting_multiplier=mar32_multiplier,
            sec_appendix_e_multiplication_factor=sec_factor,
            meets_basel_reference_sample_size=(n >= BASEL_REFERENCE_SAMPLE_SIZE),
            governing_pnl_basis=basis,
            actual_pnl_exceptions=len(actual_exceptions),
            hypothetical_pnl_exceptions=hypothetical_count,
            missing_data_outliers=missing_data_outliers,
            es_acerbi_szekely_z2=z2,
            es_underestimated=es_underestimated,
        )

    # ------------------------------------------------------------------ internals ---

    def _expected_shortfall_diagnostic(
        self,
        observations: Sequence[DailyRiskObservation],
        missing_data_outliers: int,
    ) -> Tuple[Optional[float], Optional[bool]]:
        """
        Acerbi-Szekely Z2, computed only on a complete and finite ES series.

        A partial ES series would silently change which days the mean is taken over, and a
        window containing missing-data outliers has no defined P&L for those days, so the
        statistic is withheld rather than computed on a subset that no longer matches the
        VaR backtest window.
        """
        supplied = [o for o in observations if o.forecast_es_usd is not None]
        if not supplied:
            return None, None
        if len(supplied) != len(observations):
            logger.warning(
                "forecast_es_usd supplied on only %d of %d observations; the Acerbi-"
                "Szekely Z2 statistic needs a complete series and has been skipped.",
                len(supplied), len(observations),
            )
            return None, None
        if missing_data_outliers:
            logger.warning(
                "%d observation(s) have unavailable P&L or VaR; the Acerbi-Szekely Z2 "
                "statistic is undefined on those days and has been skipped.",
                missing_data_outliers,
            )
            return None, None
        if any(not math.isfinite(o.forecast_es_usd) for o in observations):
            logger.warning(
                "forecast_es_usd contains non-finite values; the Acerbi-Szekely Z2 "
                "statistic has been skipped."
            )
            return None, None

        z2 = acerbi_szekely_z2(
            [o.realized_pnl_usd for o in observations],
            [o.forecast_var_usd for o in observations],
            [o.forecast_es_usd for o in observations],
            self.alpha,
        )
        return z2, z2 < 0.0

    def _build_audit_notes(self, **f: Any) -> str:
        """Assemble the human-readable audit trail. Full precision, no silent rounding."""
        parts = [
            f"VaR BACKTEST [{f['zone'].value}]: N={f['n']}, exceptions={f['x']} "
            f"(expected={f['expected']:.2f}, rate={f['rate_pct']:.2f}%), "
            f"basis={f['basis']} P&L.",
            f"Kupiec POF (unconditional coverage): LR={f['kupiec_stat']:.4f}, "
            f"p={f['kupiec_p']:.6g}, reject={f['kupiec_p'] < self.significance_level} "
            f"(two-sided, alpha={self.significance_level}).",
            f"Christoffersen independence: LR_ind={f['ind_stat']:.4f}, "
            f"p={f['ind_p']:.6g}; conditional coverage: LR_cc={f['cc_stat']:.4f}, "
            f"p={f['cc_p']:.6g}.",
            f"Basel zone={f['zone'].value} (yellow from {f['yellow_start']}, red from "
            f"{f['red_start']}; P(X<={f['x']})={f['cumulative']:.6f}).",
        ]
        if f["hypothetical_count"] is not None:
            parts.append(
                f"MAR32.5(1) greater-of rule applied: actual P&L exceptions="
                f"{f['actual_count']}, hypothetical P&L exceptions="
                f"{f['hypothetical_count']}."
            )
        if f["missing_data_outliers"]:
            parts.append(
                f"WARNING: {f['missing_data_outliers']} day(s) had unavailable P&L or VaR "
                "and were counted as outliers per MAR32.5(2)."
            )
        if f["clustered"]:
            parts.append(
                "WARNING: exceptions are clustered - the independence property is "
                "rejected. The model reacts too slowly to changing market conditions; a "
                "run of consecutive breaches can be harder to survive than the same "
                "number spread out, and Kupiec's count-based test cannot see this."
            )
        if f["on_published_basis"]:
            parts.append(
                f"Capital consequence at the published 250-day, 99% basis: bcbs22 Table 2 "
                f"scaling-factor increase={f['bcbs22_increase']:.2f} (on a base of 3); "
                f"MAR32.9 backtesting multiplier={f['mar32_multiplier']:.2f}; "
                f"SEC 17 CFR 240.15c3-1e Appendix E Table 1 multiplication factor="
                f"{f['sec_factor']:.2f}."
            )
        else:
            parts.append(
                "Capital multipliers not applicable: the published tables are defined "
                f"only for a {BASEL_REFERENCE_SAMPLE_SIZE}-observation window at 99% "
                "coverage. Zone boundaries above are still the bcbs22 binomial rule."
            )
        if f["n"] < BASEL_REFERENCE_SAMPLE_SIZE:
            parts.append(
                f"WARNING: {f['n']} observations is below the Basel reference window of "
                f"{BASEL_REFERENCE_SAMPLE_SIZE} trading days; the chi-square "
                "approximations and the tests' power are both degraded."
            )
        if f["zone"] == BaselZone.RED:
            parts.append(
                "RED zone: bcbs22 Sec. III(f) directs the supervisor to automatically "
                "increase the multiplication factor; MAR32.15 adds that the supervisor "
                "may disallow use of the model. This is a supervisory determination, not "
                "an automatic disqualification produced by this tool."
            )
        if f["z2"] is not None:
            direction = ("VaR and/or ES UNDERESTIMATE realised risk" if f["z2"] < 0
                         else "forecasts are not underestimating realised risk")
            parts.append(
                f"Acerbi-Szekely ES diagnostic: Z2={f['z2']:.4f} ({direction}). No p-value: "
                "its critical value requires simulating the predictive distribution."
            )
        return " ".join(parts)
