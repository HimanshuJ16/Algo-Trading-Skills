"""
risk-limit-calibration-against-historical-drawdowns: calibrates a maximum-drawdown
limit, a daily loss limit and a position-size scalar from a strategy's own realized
daily return history.

Three calibration methods are offered, and they do **not** measure the same thing:

``HISTORICAL_MAX_DD``
    ``limit = observed peak-to-trough max drawdown x stress_buffer_multiplier``.
    The only method that measures an actual drawdown. It can never exceed the worst
    outcome already in the sample, so it says nothing about a loss the strategy has
    not yet lived through.

``PARAMETRIC_VAR``
    The ``horizon_days``-day cumulative loss quantile under an IID normal model:

        loss_h = -h * mu + z_q * sigma * sqrt(h)

    Drift aggregates linearly in ``h`` and volatility with ``sqrt(h)``; scaling a
    one-day VaR (which already embeds ``-mu``) by ``sqrt(h)`` mis-scales the drift
    term and is not done here. This is a *fixed-window cumulative loss*, which is a
    lower bound on the drawdown over a window of the same length: a drawdown
    maximises over every start point inside the window, so whenever the window's
    cumulative return is negative the drawdown is at least as large.

``EXTREME_VALUE_THEORY``
    A peaks-over-threshold fit of a generalized Pareto distribution to the left tail
    of the daily returns, giving a daily tail VaR and Expected Shortfall, converted
    to the ``horizon_days`` horizon by ``sqrt(h)``. See the limitations below before
    relying on it.

Formulas and their sources
--------------------------
- Ulcer Index (Martin & McCann, 1987): the square root of the mean of the squared
  percentage drawdowns from the running peak. Computed over the whole series here,
  not over a rolling 14-period window.
- Historical (empirical) VaR/ES: order statistics of the return sample. With ``n``
  observations and confidence ``q``, ``k = ceil((1 - q) * n)``; VaR is the ``k``-th
  smallest return negated, ES the negated mean of the ``k`` smallest. ES >= VaR
  holds by construction.
- POT/GPD tail (Pickands-Balkema-de Haan): with threshold ``u``, ``N_u``
  exceedances out of ``n`` observations, shape ``xi`` and scale ``beta``,

      VaR_q = u + (beta / xi) * ( ((n / N_u) * (1 - q)) ** (-xi) - 1 )
      ES_q  = VaR_q / (1 - xi) + (beta - xi * u) / (1 - xi)      (requires xi < 1)

  with the ``xi -> 0`` limit ``VaR_q = u + beta * ln( N_u / (n * (1 - q)) )``.
- GPD parameters are fitted by method of moments, derived from the GPD moments
  ``E[Y] = beta / (1 - xi)`` and ``Var[Y] = beta^2 / ((1 - xi)^2 (1 - 2 xi))``:

      xi = (1 - mean^2 / var) / 2,   beta = mean * (1 + mean^2 / var) / 2

Limitations (documented, deliberate)
------------------------------------
- **Every threshold produced here is your own risk policy, not a regulatory
  minimum.** No rule surveyed in ``references/standards.md`` sets a drawdown or
  daily-loss number for a trading firm. The ``5%`` floor, ``50%`` cap, ``1.5x``
  buffer, ``3x`` VaR daily-loss multiple and ``20%`` position-scalar threshold are
  defaults of this module, nothing more.
- **Method of moments cannot represent a tail heavier than ``xi = 0.5``.** Because
  ``xi = (1 - mean^2 / var) / 2`` and both moments are positive, the estimator is
  structurally bounded above by ``0.5``. On a genuinely infinite-variance tail it
  therefore *understates* the tail. It is used because it is closed-form and
  verifiable; a maximum-likelihood or probability-weighted-moments fit is the right
  upgrade if the tail matters that much.
- **``sqrt(h)`` horizon scaling assumes IID returns.** Under volatility clustering
  or serial correlation it is wrong in a direction that depends on the sign of the
  autocorrelation. The EVT and parametric limits inherit that assumption; the
  historical method does not.
- **No path simulation.** Resampling the return series to get a distribution of
  maximum drawdowns is ``monte-carlo-strategy-robustness-testing``, not this module.
- **Calibration is not enforcement.** These numbers are inputs to a runtime control
  that must live outside strategy logic (``kill-switch-and-drawdown-circuit-breakers``,
  ``portfolio-level-stop-loss-independent-of-strategy-stops``).
- **Returns must be fractional returns on account equity** (``0.02`` = +2%), sampled
  daily, in chronological order, ending at the last completed session. Absolute
  currency P&L compounded as a return produces meaningless drawdowns.
"""
import logging
import math
import statistics
from dataclasses import dataclass
from enum import Enum
from statistics import NormalDist
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: Trading days per year, used only to annualize the daily volatility report.
TRADING_DAYS_PER_YEAR = 252

#: Default calibration window: one year of daily observations.
DEFAULT_MIN_OBSERVATIONS = TRADING_DAYS_PER_YEAR

#: Hard floor on the calibration window, whatever the caller passes. ~6 months of
#: trading days. Anchored to the shortest observation period a supervisor may permit
#: for a bank's ES model (Basel Framework MAR33.8(2): "no shorter than six months").
#: MAR33 binds banks, not trading firms -- this is a sanity floor, not a mandate.
ABSOLUTE_MIN_OBSERVATIONS = 126

#: Fraction of the sample used as peaks-over-threshold exceedances.
DEFAULT_EVT_TAIL_FRACTION = 0.10

#: Minimum exceedances required before a GPD tail fit is attempted. Chosen so the
#: shipped defaults are mutually consistent: ``DEFAULT_MIN_OBSERVATIONS`` (252) at
#: ``DEFAULT_EVT_TAIL_FRACTION`` (10%) yields exactly 25 exceedances. Raise it if you
#: have a longer history -- a GPD fitted to fewer points describes the sample, not
#: the tail.
DEFAULT_MIN_EXCEEDANCES = 25

#: Below this |xi| the GPD collapses to the exponential (Gumbel-domain) limit.
_XI_ZERO_TOLERANCE = 1e-8


class CalibrationError(Exception):
    """Base exception for drawdown limit calibration errors."""


class InsufficientDataError(CalibrationError):
    """Raised when the returns history is too short for the requested calibration."""


class InvalidReturnSeriesError(CalibrationError):
    """Raised when the returns series contains values that cannot be calibrated on."""


class InvalidParameterError(CalibrationError):
    """Raised when an engine or call parameter is outside its supported domain."""


class TailFitError(CalibrationError):
    """Raised when the peaks-over-threshold GPD tail cannot be fitted or used."""


class CalibrationMethod(str, Enum):
    """How the maximum-drawdown limit is derived. See the module docstring."""

    HISTORICAL_MAX_DD = "HISTORICAL_MAX_DD"
    PARAMETRIC_VAR = "PARAMETRIC_VAR"
    EXTREME_VALUE_THEORY = "EXTREME_VALUE_THEORY"


@dataclass(frozen=True)
class GpdTailFit:
    """Peaks-over-threshold generalized Pareto fit of the left (loss) tail.

    All percentage fields are positive loss magnitudes in percent.
    """

    threshold_loss_pct: float
    exceedances: int
    observations: int
    shape_xi: float
    scale_beta: float
    var_pct: float
    cvar_pct: float


@dataclass(frozen=True)
class DrawdownMetrics:
    """Realized risk metrics of a daily return series.

    ``var_pct`` and ``cvar_pct`` are *historical* (order-statistic) estimates at
    ``confidence_level_pct``, expressed as positive loss magnitudes in percent. They
    are zero when the sample's tail observations are non-negative, which is a real
    result about the sample, not an error.
    """

    observations: int
    max_drawdown_pct: float
    max_drawdown_duration_days: int
    drawdown_unrecovered: bool
    ulcer_index: float
    mean_daily_return_pct: float
    daily_volatility_pct: float
    volatility_annualized: float
    confidence_level_pct: float
    var_pct: float
    cvar_pct: float


@dataclass(frozen=True)
class CalibratedRiskLimits:
    """Calibrated limits plus the evidence needed to audit how they were derived."""

    portfolio_capital_usd: float
    calibrated_max_drawdown_pct: float
    calibrated_max_drawdown_usd: float
    calibrated_daily_loss_limit_usd: float
    position_size_scalar: float
    confidence_level_pct: float
    calibration_method: CalibrationMethod
    horizon_days: int
    limit_basis: str
    floor_binding: bool
    cap_binding: bool
    metrics: DrawdownMetrics
    tail_fit: Optional[GpdTailFit]
    audit_notes: str


def _validate_return_series(
    daily_returns: Sequence[float], min_observations: int
) -> List[float]:
    """Rejects anything that cannot be calibrated on, rather than calibrating on it.

    A single non-finite return silently propagates a ``NaN`` into the daily loss
    limit, and a ``NaN`` limit is never breached by any comparison -- a risk control
    that can never fire. A return at or below ``-1.0`` drives the equity curve to
    zero or negative, after which every subsequent drawdown figure is meaningless.
    """
    if isinstance(daily_returns, (str, bytes)) or not isinstance(
        daily_returns, Sequence
    ):
        raise InvalidReturnSeriesError(
            "daily_returns must be a sequence of float returns"
        )

    values: List[float] = []
    for index, value in enumerate(daily_returns):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise InvalidReturnSeriesError(
                f"daily_returns[{index}] is {value!r}; expected a float return"
            )
        as_float = float(value)
        if not math.isfinite(as_float):
            raise InvalidReturnSeriesError(
                f"daily_returns[{index}] is non-finite ({value!r}); resolve the gap "
                "in the return series rather than calibrating a limit on it"
            )
        if as_float <= -1.0:
            raise InvalidReturnSeriesError(
                f"daily_returns[{index}] = {as_float} wipes out or inverts account "
                "equity; returns must be fractional returns on equity, > -1.0"
            )
        values.append(as_float)

    if len(values) < min_observations:
        raise InsufficientDataError(
            f"{len(values)} observations supplied but {min_observations} are "
            "required; a tail limit calibrated on a shorter window mostly reflects "
            "which regime happened to be sampled"
        )
    return values


def _historical_var_cvar(
    returns: Sequence[float], confidence_pct: float
) -> Tuple[float, float]:
    """Order-statistic (historical simulation) VaR and Expected Shortfall.

    Returns ``(var, cvar)`` as non-negative loss fractions. ``k = ceil((1-q) * n)``
    tail observations are used; the engine's constructor already guarantees that
    ``k >= 1`` is attainable for the configured confidence and window.
    """
    n = len(returns)
    tail_probability = 1.0 - confidence_pct / 100.0
    k = math.ceil(tail_probability * n)
    worst = sorted(returns)[:k]
    var = max(0.0, -worst[-1])
    cvar = max(0.0, -statistics.fmean(worst))
    # ES >= VaR by construction (the mean of the k worst is <= the k-th worst); the
    # max(0, .) clamps can only disturb that when both are clamped to zero.
    return var, max(cvar, var)


def fit_gpd_left_tail(
    daily_returns: Sequence[float],
    confidence_pct: float,
    tail_fraction: float = DEFAULT_EVT_TAIL_FRACTION,
    min_exceedances: int = DEFAULT_MIN_EXCEEDANCES,
) -> GpdTailFit:
    """Fits a generalized Pareto distribution to the left tail by peaks-over-threshold.

    The threshold is the ``(N_u + 1)``-th largest loss, so exactly ``N_u`` losses
    exceed it. Parameters are fitted by method of moments on the excesses; see the
    module docstring for the estimator, the quantile formula and their limitations.

    Raises:
        InvalidParameterError: ``tail_fraction`` or ``min_exceedances`` out of domain.
        TailFitError: too few exceedances, a degenerate excess sample, or a
            confidence level that does not lie above the fitted threshold.
    """
    if not 0.0 < tail_fraction < 1.0:
        raise InvalidParameterError(
            f"tail_fraction must be in (0, 1), got {tail_fraction}"
        )
    if min_exceedances < 2:
        raise InvalidParameterError(
            f"min_exceedances must be at least 2, got {min_exceedances}"
        )

    n = len(daily_returns)
    losses = sorted((-r for r in daily_returns), reverse=True)
    n_exceed = int(n * tail_fraction)
    if n_exceed < min_exceedances:
        raise TailFitError(
            f"{n_exceed} exceedances at tail_fraction={tail_fraction} on {n} "
            f"observations, but {min_exceedances} are required; a GPD fitted to a "
            "handful of points reports the sample, not the tail"
        )
    if n_exceed >= n:
        raise TailFitError("tail_fraction leaves no observations below the threshold")

    threshold = losses[n_exceed]
    excesses = [loss - threshold for loss in losses[:n_exceed]]
    mean_excess = statistics.fmean(excesses)
    if mean_excess <= 0.0:
        raise TailFitError(
            "all exceedances are tied with the threshold; the left tail is "
            "degenerate and cannot be fitted"
        )
    variance_excess = statistics.variance(excesses)
    if variance_excess <= 0.0:
        raise TailFitError(
            "exceedances have zero variance; the left tail is degenerate and cannot "
            "be fitted"
        )

    moment_ratio = mean_excess * mean_excess / variance_excess
    xi = 0.5 * (1.0 - moment_ratio)
    beta = 0.5 * mean_excess * (1.0 + moment_ratio)
    if beta <= 0.0:
        raise TailFitError(f"fitted GPD scale is non-positive (beta={beta})")
    if xi >= 1.0:
        # Unreachable via method of moments (which bounds xi < 0.5); kept so that
        # swapping in an MLE fit cannot silently produce an infinite-mean tail.
        raise TailFitError(
            f"fitted GPD shape xi={xi:.4f} >= 1; the tail has infinite mean and no "
            "finite Expected Shortfall exists"
        )
    if xi >= 0.25:
        logger.warning(
            "GPD shape xi=%.4f is at the edge of the method-of-moments estimator's "
            "domain (it requires xi < 0.25 for its own variance to be finite); treat "
            "the tail estimate as indicative only",
            xi,
        )

    tail_probability = 1.0 - confidence_pct / 100.0
    scaled_probability = (n / n_exceed) * tail_probability
    if scaled_probability >= 1.0:
        raise TailFitError(
            f"confidence {confidence_pct}% lies below the fitted threshold "
            f"(exceedance rate {n_exceed / n:.4f}); POT cannot interpolate there -- "
            "lower tail_fraction or raise the confidence level"
        )

    if abs(xi) < _XI_ZERO_TOLERANCE:
        var = threshold + beta * math.log(1.0 / scaled_probability)
    else:
        var = threshold + (beta / xi) * (scaled_probability ** (-xi) - 1.0)
    cvar = var / (1.0 - xi) + (beta - xi * threshold) / (1.0 - xi)

    return GpdTailFit(
        threshold_loss_pct=round(threshold * 100.0, 6),
        exceedances=n_exceed,
        observations=n,
        shape_xi=round(xi, 8),
        scale_beta=round(beta, 10),
        var_pct=round(max(0.0, var) * 100.0, 6),
        cvar_pct=round(max(0.0, cvar) * 100.0, 6),
    )


class DrawdownLimitCalibratorEngine:
    """Calibrates drawdown, daily-loss and position-size limits from realized returns.

    Every default below is this module's own risk policy. Nothing surveyed in
    ``references/standards.md`` mandates any of these numbers for a trading firm.

    Args:
        stress_buffer_multiplier: Headroom over the calibrated risk figure. Must be
            >= 1.0 -- a multiplier below 1 sets a limit tighter than a loss the
            strategy has already survived, guaranteeing a halt on a repeat.
        target_confidence_pct: One-tailed confidence for every VaR/ES figure. Drives
            the calculation, not just the report.
        min_observations: Calibration window length. Floored at
            ``ABSOLUTE_MIN_OBSERVATIONS``.
        horizon_days: Horizon for the ``PARAMETRIC_VAR`` and ``EXTREME_VALUE_THEORY``
            limits. Ignored by ``HISTORICAL_MAX_DD``.
        drawdown_limit_floor_pct: Lower bound on the calibrated drawdown limit, so a
            benign sample cannot produce a limit that trips on ordinary noise.
        drawdown_limit_cap_pct: Upper bound, so a catastrophic sample cannot produce
            a limit large enough to be no limit at all.
        daily_loss_var_multiple: Multiple of the daily VaR used as the daily loss
            limit.
        position_scalar_threshold_pct: Historical drawdown above which the position
            scalar is reduced below 1.0.
        evt_tail_fraction: Fraction of the sample used as POT exceedances.
        evt_min_exceedances: Minimum exceedances required for a GPD fit.
    """

    def __init__(
        self,
        stress_buffer_multiplier: float = 1.5,
        target_confidence_pct: float = 99.0,
        min_observations: int = DEFAULT_MIN_OBSERVATIONS,
        horizon_days: int = 20,
        drawdown_limit_floor_pct: float = 5.0,
        drawdown_limit_cap_pct: float = 50.0,
        daily_loss_var_multiple: float = 3.0,
        position_scalar_threshold_pct: float = 20.0,
        evt_tail_fraction: float = DEFAULT_EVT_TAIL_FRACTION,
        evt_min_exceedances: int = DEFAULT_MIN_EXCEEDANCES,
    ) -> None:
        if (
            not math.isfinite(stress_buffer_multiplier)
            or stress_buffer_multiplier < 1.0
        ):
            raise InvalidParameterError(
                f"stress_buffer_multiplier must be >= 1.0, got "
                f"{stress_buffer_multiplier}; a limit below the realized loss "
                "guarantees a halt the next time that loss repeats"
            )
        if not 50.0 < target_confidence_pct < 100.0:
            raise InvalidParameterError(
                f"target_confidence_pct must be in (50, 100), got "
                f"{target_confidence_pct}"
            )
        if min_observations < ABSOLUTE_MIN_OBSERVATIONS:
            raise InvalidParameterError(
                f"min_observations must be >= {ABSOLUTE_MIN_OBSERVATIONS}, got "
                f"{min_observations}"
            )
        tail_probability = 1.0 - target_confidence_pct / 100.0
        if min_observations * tail_probability < 1.0:
            raise InvalidParameterError(
                f"a {target_confidence_pct}% tail needs at least "
                f"{math.ceil(1.0 / tail_probability)} observations to contain a "
                f"single loss; min_observations={min_observations} cannot support it"
            )
        if horizon_days < 1:
            raise InvalidParameterError(
                f"horizon_days must be >= 1, got {horizon_days}"
            )
        if not 0.0 < drawdown_limit_floor_pct < drawdown_limit_cap_pct <= 100.0:
            raise InvalidParameterError(
                "require 0 < drawdown_limit_floor_pct < drawdown_limit_cap_pct "
                f"<= 100, got floor={drawdown_limit_floor_pct}, "
                f"cap={drawdown_limit_cap_pct}"
            )
        if (
            not math.isfinite(daily_loss_var_multiple)
            or daily_loss_var_multiple <= 0.0
        ):
            raise InvalidParameterError(
                f"daily_loss_var_multiple must be > 0, got {daily_loss_var_multiple}"
            )
        if not 0.0 < position_scalar_threshold_pct <= 100.0:
            raise InvalidParameterError(
                "position_scalar_threshold_pct must be in (0, 100], got "
                f"{position_scalar_threshold_pct}"
            )

        self.stress_buffer_multiplier = float(stress_buffer_multiplier)
        self.target_confidence_pct = float(target_confidence_pct)
        self.min_observations = int(min_observations)
        self.horizon_days = int(horizon_days)
        self.drawdown_limit_floor_pct = float(drawdown_limit_floor_pct)
        self.drawdown_limit_cap_pct = float(drawdown_limit_cap_pct)
        self.daily_loss_var_multiple = float(daily_loss_var_multiple)
        self.position_scalar_threshold_pct = float(position_scalar_threshold_pct)
        self.evt_tail_fraction = float(evt_tail_fraction)
        self.evt_min_exceedances = int(evt_min_exceedances)

    def compute_drawdown_metrics(
        self, daily_returns: Sequence[float]
    ) -> DrawdownMetrics:
        """Computes realized drawdown, Ulcer Index and historical VaR/ES.

        The drawdown duration is the longest run of consecutive observations spent
        strictly below the running peak. A day that closes exactly at the peak is
        not underwater and does not extend the run. When the series ends below its
        peak the run is still open, so the duration is right-censored --
        ``drawdown_unrecovered`` flags that.

        Raises:
            InvalidReturnSeriesError: non-finite return, or a return <= -1.0.
            InsufficientDataError: fewer than ``min_observations`` observations.
        """
        returns = _validate_return_series(daily_returns, self.min_observations)
        n = len(returns)

        equity = 1.0
        peak = 1.0
        max_dd = 0.0
        current_run = 0
        max_run = 0
        squared_drawdowns = 0.0

        for r in returns:
            equity *= 1.0 + r
            if equity >= peak:
                peak = equity
                current_run = 0
            else:
                current_run += 1
                max_run = max(max_run, current_run)
            drawdown = (peak - equity) / peak
            max_dd = max(max_dd, drawdown)
            squared_drawdowns += (drawdown * 100.0) ** 2

        ulcer_index = math.sqrt(squared_drawdowns / n)

        daily_mean = statistics.fmean(returns)
        daily_std = statistics.stdev(returns)
        var, cvar = _historical_var_cvar(returns, self.target_confidence_pct)

        return DrawdownMetrics(
            observations=n,
            max_drawdown_pct=round(max_dd * 100.0, 6),
            max_drawdown_duration_days=max_run,
            drawdown_unrecovered=current_run > 0,
            ulcer_index=round(ulcer_index, 6),
            mean_daily_return_pct=round(daily_mean * 100.0, 8),
            daily_volatility_pct=round(daily_std * 100.0, 8),
            volatility_annualized=round(
                daily_std * math.sqrt(TRADING_DAYS_PER_YEAR) * 100.0, 6
            ),
            confidence_level_pct=self.target_confidence_pct,
            var_pct=round(var * 100.0, 6),
            cvar_pct=round(cvar * 100.0, 6),
        )

    def calibrate_risk_limits(
        self,
        daily_returns: Sequence[float],
        portfolio_capital_usd: float,
        method: CalibrationMethod = CalibrationMethod.HISTORICAL_MAX_DD,
    ) -> CalibratedRiskLimits:
        """Calibrates the drawdown limit, daily loss limit and position-size scalar.

        All intermediate quantities are carried at full precision; rounding happens
        only when the result objects are built.

        Raises:
            InvalidParameterError: non-positive/non-finite capital, or a value that
                is not a ``CalibrationMethod``.
            InvalidReturnSeriesError / InsufficientDataError: see
                ``compute_drawdown_metrics``.
            TailFitError: ``EXTREME_VALUE_THEORY`` requested but the tail cannot be
                fitted.
            CalibrationError: the sample's tail loss is non-positive, so no
                meaningful daily loss limit exists.
        """
        if isinstance(portfolio_capital_usd, bool) or not isinstance(
            portfolio_capital_usd, (int, float)
        ):
            raise InvalidParameterError(
                f"portfolio_capital_usd must be a number, got "
                f"{portfolio_capital_usd!r}"
            )
        capital = float(portfolio_capital_usd)
        if not math.isfinite(capital) or capital <= 0.0:
            raise InvalidParameterError(
                f"portfolio_capital_usd must be finite and > 0, got {capital}; "
                "non-positive capital produces negative limits that no comparison "
                "can enforce"
            )
        if not isinstance(method, CalibrationMethod):
            raise InvalidParameterError(
                f"method must be a CalibrationMethod, got {method!r}"
            )

        returns = _validate_return_series(daily_returns, self.min_observations)
        metrics = self.compute_drawdown_metrics(returns)
        horizon = self.horizon_days
        tail_fit: Optional[GpdTailFit] = None

        if method is CalibrationMethod.HISTORICAL_MAX_DD:
            raw_pct = metrics.max_drawdown_pct * self.stress_buffer_multiplier
            basis = (
                f"observed peak-to-trough max drawdown {metrics.max_drawdown_pct}% "
                f"x {self.stress_buffer_multiplier}x stress buffer; bounded by the "
                "worst outcome in the sample"
            )
        elif method is CalibrationMethod.PARAMETRIC_VAR:
            z = NormalDist().inv_cdf(self.target_confidence_pct / 100.0)
            mean_daily = statistics.fmean(returns)
            std_daily = statistics.stdev(returns)
            loss_h = -horizon * mean_daily + z * std_daily * math.sqrt(horizon)
            raw_pct = max(0.0, loss_h) * 100.0 * self.stress_buffer_multiplier
            basis = (
                f"IID-normal {horizon}-day cumulative loss quantile at "
                f"{self.target_confidence_pct}% (drift scaled by h, volatility by "
                f"sqrt(h)) x {self.stress_buffer_multiplier}x stress buffer; a lower "
                "bound on the drawdown over a window of the same length"
            )
        elif method is CalibrationMethod.EXTREME_VALUE_THEORY:
            tail_fit = fit_gpd_left_tail(
                returns,
                confidence_pct=self.target_confidence_pct,
                tail_fraction=self.evt_tail_fraction,
                min_exceedances=self.evt_min_exceedances,
            )
            raw_pct = (
                tail_fit.cvar_pct * math.sqrt(horizon) * self.stress_buffer_multiplier
            )
            basis = (
                f"POT/GPD daily Expected Shortfall {tail_fit.cvar_pct}% "
                f"(xi={tail_fit.shape_xi}, beta={tail_fit.scale_beta}, "
                f"{tail_fit.exceedances} exceedances) scaled to {horizon} days by "
                f"sqrt(h) x {self.stress_buffer_multiplier}x stress buffer; the "
                "sqrt(h) step assumes IID returns"
            )
        else:  # pragma: no cover - CalibrationMethod is exhaustively handled above
            raise InvalidParameterError(
                f"unhandled calibration method {method!r}; refusing to fall back to "
                "a different method than the one recorded in the audit trail"
            )

        floor_binding = raw_pct < self.drawdown_limit_floor_pct
        cap_binding = raw_pct > self.drawdown_limit_cap_pct
        calibrated_dd_pct = min(
            max(raw_pct, self.drawdown_limit_floor_pct), self.drawdown_limit_cap_pct
        )
        calibrated_dd_usd = capital * (calibrated_dd_pct / 100.0)

        if metrics.var_pct <= 0.0:
            raise CalibrationError(
                f"historical {self.target_confidence_pct}% tail loss is "
                f"{metrics.var_pct}% -- the sample contains no loss at that "
                "confidence, so no daily loss limit can be calibrated from it; "
                "extend the window to include a losing regime"
            )
        daily_loss_usd = (
            capital * (metrics.var_pct / 100.0) * self.daily_loss_var_multiple
        )

        if metrics.max_drawdown_pct > self.position_scalar_threshold_pct:
            position_scalar = (
                self.position_scalar_threshold_pct / metrics.max_drawdown_pct
            )
        else:
            position_scalar = 1.0

        notes = (
            f"RISK CALIBRATION [{method.value}]: capital=${capital:,.2f}, "
            f"observations={metrics.observations}, "
            f"hist_max_dd={metrics.max_drawdown_pct}%, "
            f"hist_var_{self.target_confidence_pct:g}={metrics.var_pct}%, "
            f"hist_cvar_{self.target_confidence_pct:g}={metrics.cvar_pct}%, "
            f"calibrated_max_dd={calibrated_dd_pct:.6f}% "
            f"(${calibrated_dd_usd:,.2f}), "
            f"daily_loss_limit=${daily_loss_usd:,.2f}, "
            f"position_scalar={position_scalar:.4f}, "
            f"floor_binding={floor_binding}, cap_binding={cap_binding}. "
            f"BASIS: {basis}. "
            "These thresholds are this desk's own risk policy, not a regulatory "
            "minimum."
        )
        logger.info(notes)
        if floor_binding:
            logger.warning(
                "calibrated drawdown limit was set by the %s%% policy floor, not by "
                "the return sample (%s raw value %.6f%%)",
                self.drawdown_limit_floor_pct,
                method.value,
                raw_pct,
            )
        if cap_binding:
            logger.warning(
                "calibrated drawdown limit was truncated by the %s%% policy cap (%s "
                "raw value %.6f%%); the strategy's measured risk exceeds the largest "
                "limit this engine will issue",
                self.drawdown_limit_cap_pct,
                method.value,
                raw_pct,
            )
        if metrics.drawdown_unrecovered:
            logger.warning(
                "the return series ends below its equity peak; "
                "max_drawdown_duration_days=%d is right-censored and the true "
                "recovery time is not yet observable",
                metrics.max_drawdown_duration_days,
            )

        return CalibratedRiskLimits(
            portfolio_capital_usd=capital,
            calibrated_max_drawdown_pct=round(calibrated_dd_pct, 6),
            calibrated_max_drawdown_usd=round(calibrated_dd_usd, 2),
            calibrated_daily_loss_limit_usd=round(daily_loss_usd, 2),
            position_size_scalar=round(position_scalar, 6),
            confidence_level_pct=self.target_confidence_pct,
            calibration_method=method,
            horizon_days=horizon,
            limit_basis=basis,
            floor_binding=floor_binding,
            cap_binding=cap_binding,
            metrics=metrics,
            tail_fit=tail_fit,
            audit_notes=notes,
        )
