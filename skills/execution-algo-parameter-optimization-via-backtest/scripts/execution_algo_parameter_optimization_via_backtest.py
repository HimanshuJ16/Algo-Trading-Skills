"""
execution-algo-parameter-optimization-via-backtest: grid-search optimizer for
execution-algorithm parameters (participation ceiling, risk aversion, peg offset),
scored by Implementation Shortfall replayed over historical intraday price paths.

Each candidate parameter set is replayed against every historical trade sample:

    1. Almgren & Chriss (2000) Eq. (17) gives the target inventory trajectory
       x_j = X * sinh(kappa*(T - t_j)) / sinh(kappa*T), with the decay kappa set by
       the risk-aversion parameter lambda (ibid. Eq. 19, kappa ~ sqrt(lambda*sigma^2/eta)).
       Higher lambda front-loads the schedule: more impact, less exposure to the path.
    2. The per-interval target is capped at max_participation_rate * interval volume,
       so an aggressive schedule on a thin interval leaves an unfilled remainder that
       rolls forward -- fill completion is an *outcome* of the path, not an assumption.
    3. Each slice prices off the observed historical market price, displaced by
       permanent impact accumulated from our own prior fills, plus temporary impact
       and the peg/spread concession paid on that slice.
    4. Implementation Shortfall follows Perold (1988): the fill-weighted execution
       cost against the arrival (decision) price PLUS the opportunity cost of the
       quantity left unexecuted, valued at the terminal price of the path.

Market impact uses the empirically fitted model of Almgren, Thum, Hauptmann & Li
(2005), "Direct Estimation of Equity Market Impact", Risk 18(7), 57-62 -- the
"universal coefficients" summarised in their Section 4.3:

    permanent   I = gamma * sigma * (X/V) * (Theta/V)^(1/4)      gamma = 0.314 +/- 0.041
    temporary   K = eta   * sigma * (X/(V*T))^beta               eta   = 0.142 +/- 0.0062
                                                                 beta  = 0.600 +/- 0.038
    realised cost on the order   J = I/2 + K

with sigma the daily volatility as a fraction, X order shares, V average daily
volume, Theta shares outstanding, and X/(V*T) the participation rate. ATHL rejected
the square-root exponent beta = 1/2 at the 95% confidence level in favour of 3/5
(ibid. Sec. 4.2); this module therefore does NOT use a square-root impact law.

Limitations (documented, deliberate -- read before acting on an optimum):

- **The ATHL coefficients are not universal constants.** They were fitted to
  Citigroup US large-cap equity desk flow from 2001-2003. Their own R^2 is under one
  percent (ibid. Sec. 4.3): the model predicts the *expectation* of cost, and any
  individual order varies enormously around it. Recalibrate `ImpactModelCoefficients`
  against your own realised TCA before treating an optimum as actionable, and see
  `execution-cost-model-recalibration-cadence` for the cadence.
- **lambda is on this module's scale, not a universal quantity.** kappa is obtained
  from AC Eq. (19) with eta linearised from the ATHL temporary-impact cost at the
  participation ceiling over a one-day reference horizon. That linearisation is an
  approximation (ATHL's h carries sigma, so its units differ from AC's linear eta by
  a day^(1/2) factor that is unity only at a one-day horizon). The resulting lambda
  ordering is meaningful; its absolute magnitude is not transferable to another
  implementation.
- **Impact is modelled, not observed.** The historical path supplies realised price
  movement and volume; it cannot tell you what the market would have done had you
  traded into it. Backtested impact is only ever as good as the calibration.
- **No queue position, no venue routing, no order-book depth, no lot rounding.**
  Fills are a participation-capped schedule, not a matching-engine simulation, and
  slice quantities stay fractional so that lot quantisation does not contaminate the
  cost comparison. See `execution-realistic-simulation`,
  `queue-position-modeling-for-passive-orders` and
  `minimum-fill-size-and-lot-rounding-logic`.
- **Selection on in-sample data overfits.** Pass `holdout_samples` and read the
  degradation figure; a candidate chosen without one carries an explicit warning in
  the report and must not be promoted on that basis alone.

References
----------
Almgren, R. & Chriss, N. (2000). "Optimal Execution of Portfolio Transactions",
    Journal of Risk 3(2), 5-39. Eq. (17) trajectory, Eq. (7) temporary impact with
    the epsilon spread term, Eq. (19) kappa ~ sqrt(lambda*sigma^2/eta).
Almgren, R., Thum, C., Hauptmann, E. & Li, H. (2005). "Direct Estimation of Equity
    Market Impact", Risk 18(7), 57-62.
Perold, A. F. (1988). "The Implementation Shortfall: Paper Versus Reality",
    Journal of Portfolio Management 14(3), 4-9.
"""
import logging
import math
import statistics
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: Permanent impact coefficient gamma (ATHL 2005, Sec. 4.3: 0.314 +/- 0.041).
ATHL_PERMANENT_COEFFICIENT = 0.314

#: Liquidity exponent on Theta/V (ATHL 2005, Sec. 4.2: "very approximately delta = 1/4").
ATHL_LIQUIDITY_EXPONENT = 0.25

#: Temporary impact coefficient eta (ATHL 2005, Sec. 4.3: 0.142 +/- 0.0062).
ATHL_TEMPORARY_COEFFICIENT = 0.142

#: Temporary impact exponent beta (ATHL 2005, Sec. 4.2: 0.600 +/- 0.038; beta = 1/2
#: rejected at the 95% level, so this is NOT a square-root law).
ATHL_TEMPORARY_EXPONENT = 0.600

#: Default participation ceiling above which candidates are not eligible for
#: selection. 25% of ADTV is the volume *condition* of the SEC Rule 10b-18(b)(4)
#: safe harbour for issuer repurchases (17 CFR 240.10b-18) -- it is a condition of
#: that safe harbour for that specific activity, NOT a general limit on agency or
#: proprietary algorithmic trading. Treat this default as a house risk limit and set
#: it from your own policy; see `algo-parameter-defaults-by-instrument-liquidity-tier`.
DEFAULT_MAX_ALLOWED_PARTICIPATION_RATE = 0.25

#: Below this kappa*T the sinh trajectory is numerically indistinguishable from the
#: risk-neutral linear (TWAP) limit, and sinh(0)/sinh(0) is undefined.
_KAPPA_T_LINEAR_LIMIT = 1e-8

#: Holdout mean-IS degradation (bps) beyond which the selected config is flagged as
#: likely overfitted. A reporting threshold, not a published standard.
DEFAULT_OVERFIT_DEGRADATION_BPS = 5.0

_VALID_SIDES = ("BUY", "SELL")


@dataclass(frozen=True)
class ImpactModelCoefficients:
    """ATHL (2005) impact coefficients. Override after recalibrating on your own flow."""
    permanent_gamma: float = ATHL_PERMANENT_COEFFICIENT
    liquidity_delta: float = ATHL_LIQUIDITY_EXPONENT
    temporary_eta: float = ATHL_TEMPORARY_COEFFICIENT
    temporary_beta: float = ATHL_TEMPORARY_EXPONENT

    def __post_init__(self) -> None:
        for name in ("permanent_gamma", "liquidity_delta", "temporary_eta", "temporary_beta"):
            value = getattr(self, name)
            if not _is_finite(value) or value < 0.0:
                raise ValueError(f"ImpactModelCoefficients.{name} must be finite and >= 0, got {value!r}")
        if self.temporary_beta <= 0.0:
            raise ValueError("temporary_beta must be > 0 so that impact vanishes as the trade rate does")


@dataclass(frozen=True)
class AlgoParameterConfig:
    """One point of the search grid."""
    max_participation_rate: float       # fraction of interval volume, e.g. 0.10 = 10%
    risk_aversion_lambda: float         # AC risk aversion on this module's scale, e.g. 1e-5
    peg_offset_ticks: int               # 0 = mid-peg, 1 = one tick through, 2 = two ticks

    def __post_init__(self) -> None:
        if not _is_finite(self.max_participation_rate) or not 0.0 < self.max_participation_rate <= 1.0:
            raise ValueError(
                f"max_participation_rate must be finite in (0, 1], got {self.max_participation_rate!r}")
        if not _is_finite(self.risk_aversion_lambda) or self.risk_aversion_lambda < 0.0:
            raise ValueError(
                f"risk_aversion_lambda must be finite and >= 0, got {self.risk_aversion_lambda!r}")
        if int(self.peg_offset_ticks) != self.peg_offset_ticks or self.peg_offset_ticks < 0:
            raise ValueError(
                f"peg_offset_ticks must be a non-negative whole number, got {self.peg_offset_ticks!r}")

    def describe(self) -> str:
        return (f"part_rate={self.max_participation_rate:.4g}, "
                f"lambda={self.risk_aversion_lambda:.3g}, "
                f"peg_ticks={self.peg_offset_ticks}")


@dataclass
class HistoricalTradeSample:
    """
    One historical parent order replayed by the backtest.

    ``historical_execution_prices`` is the observed *market* price path over the
    execution horizon, one price per interval -- not our own fills. ``arrival_price``
    is the separate decision/arrival benchmark that Implementation Shortfall is
    measured against (Perold 1988).
    """
    trade_id: str
    symbol: str
    order_qty: int
    arrival_price: float
    market_adv_shares: float
    volatility_daily_pct: float          # daily volatility as a fraction, e.g. 0.015 = 1.5%
    historical_execution_prices: List[float]
    side: str = "BUY"
    interval_volumes: Optional[List[float]] = None   # market volume per interval, shares
    execution_horizon_days: float = 1.0              # fraction of a trading day the path spans
    tick_size: float = 0.01
    shares_outstanding: Optional[float] = None       # Theta; permanent impact needs it

    def __post_init__(self) -> None:
        if self.order_qty <= 0:
            raise ValueError(f"{self.trade_id}: order_qty must be > 0, got {self.order_qty!r}")
        if not _is_finite(self.arrival_price) or self.arrival_price <= 0.0:
            raise ValueError(f"{self.trade_id}: arrival_price must be finite and > 0, got {self.arrival_price!r}")
        if not _is_finite(self.market_adv_shares) or self.market_adv_shares <= 0.0:
            raise ValueError(f"{self.trade_id}: market_adv_shares must be finite and > 0, got {self.market_adv_shares!r}")
        if not _is_finite(self.volatility_daily_pct) or self.volatility_daily_pct < 0.0:
            raise ValueError(f"{self.trade_id}: volatility_daily_pct must be finite and >= 0, got {self.volatility_daily_pct!r}")
        if not _is_finite(self.execution_horizon_days) or self.execution_horizon_days <= 0.0:
            raise ValueError(f"{self.trade_id}: execution_horizon_days must be finite and > 0, got {self.execution_horizon_days!r}")
        if not _is_finite(self.tick_size) or self.tick_size < 0.0:
            raise ValueError(f"{self.trade_id}: tick_size must be finite and >= 0, got {self.tick_size!r}")

        side = str(self.side).upper()
        if side not in _VALID_SIDES:
            raise ValueError(f"{self.trade_id}: side must be one of {_VALID_SIDES}, got {self.side!r}")
        self.side = side

        if not self.historical_execution_prices:
            raise ValueError(f"{self.trade_id}: historical_execution_prices must contain at least one price")
        for price in self.historical_execution_prices:
            if not _is_finite(price) or price <= 0.0:
                raise ValueError(f"{self.trade_id}: non-finite or non-positive price in historical_execution_prices")

        n_intervals = len(self.historical_execution_prices)
        if self.interval_volumes is None:
            # Uniform volume profile over the horizon. A real intraday volume curve is
            # U-shaped; supply interval_volumes explicitly when that matters, because a
            # flat profile understates the capacity available at the open and close.
            per_interval = self.market_adv_shares * self.execution_horizon_days / n_intervals
            self.interval_volumes = [per_interval] * n_intervals
        else:
            if len(self.interval_volumes) != n_intervals:
                raise ValueError(
                    f"{self.trade_id}: interval_volumes has {len(self.interval_volumes)} entries but "
                    f"historical_execution_prices has {n_intervals}")
            for volume in self.interval_volumes:
                if not _is_finite(volume) or volume < 0.0:
                    raise ValueError(f"{self.trade_id}: non-finite or negative entry in interval_volumes")

        if self.shares_outstanding is not None:
            if not _is_finite(self.shares_outstanding) or self.shares_outstanding <= 0.0:
                raise ValueError(f"{self.trade_id}: shares_outstanding must be finite and > 0 when supplied")

    @property
    def side_sign(self) -> int:
        """+1 when paying up hurts (BUY), -1 when receiving less hurts (SELL)."""
        return 1 if self.side == "BUY" else -1


@dataclass
class ExecutionSimulationResult:
    """Outcome of replaying one config against one historical trade sample."""
    trade_id: str
    implementation_shortfall_bps: float
    fill_completion_rate: float
    execution_cost_bps: float            # fill-weighted VWAP vs arrival, on the filled part
    opportunity_cost_bps: float          # terminal price vs arrival, on the unfilled part
    achieved_vwap: float                 # 0.0 when nothing filled
    filled_qty: float
    intervals_simulated: int
    kappa_t: float                       # AC dimensionless decay actually applied
    permanent_impact_applied: bool       # False when shares_outstanding was not supplied


@dataclass
class OptimizationCandidateResult:
    config: AlgoParameterConfig
    mean_implementation_shortfall_bps: float
    std_implementation_shortfall_bps: float
    avg_fill_completion_rate: float
    utility_score: float
    worst_implementation_shortfall_bps: float = 0.0
    min_fill_completion_rate: float = 0.0
    samples_evaluated: int = 0
    #: stdev(IS)/sqrt(n) -- how precisely this candidate's mean IS is even known.
    mean_is_standard_error_bps: float = 0.0


@dataclass
class AlgoOptimizationAuditReport:
    algo_name: str
    symbol: str
    total_trade_samples_tested: int
    total_grid_candidates_evaluated: int
    optimal_config: AlgoParameterConfig
    optimal_utility_score: float
    optimal_mean_is_bps: float
    optimal_std_is_bps: float
    all_candidate_results: List[OptimizationCandidateResult]
    audit_notes: str
    rejected_configs: List[Tuple[AlgoParameterConfig, str]] = field(default_factory=list)
    holdout_evaluated: bool = False
    holdout_mean_is_bps: Optional[float] = None
    holdout_fill_completion_rate: Optional[float] = None
    holdout_is_degradation_bps: Optional[float] = None
    #: Utility-score gap between the winner and the runner-up.
    selection_margin_score: Optional[float] = None
    #: False when that gap is inside the sampling noise on the two candidates' mean IS.
    selection_is_separated: Optional[bool] = None
    warnings: List[str] = field(default_factory=list)


def _is_finite(value: object) -> bool:
    """True only for a real, finite number. Rejects NaN, +/-Inf, bool and non-numerics."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def athl_permanent_impact_fraction(
    volatility_daily: float,
    order_qty: float,
    adv_shares: float,
    shares_outstanding: float,
    coefficients: Optional[ImpactModelCoefficients] = None,
) -> float:
    """
    Permanent impact I as a fraction of the arrival price (ATHL 2005, Sec. 4.3):

        I = gamma * sigma * (X / V) * (Theta / V) ** (1/4)

    Reproduces their Table 3: sigma = 1.57%, X/V = 0.1, Theta/V = 263 gives 19.85 bp
    against the 20 bp printed in the paper.

    I is the net price displacement caused by the order, not a cost by itself; the
    cost realised on the order is I/2 plus temporary impact (ibid.).
    """
    coefficients = coefficients or ImpactModelCoefficients()
    if adv_shares <= 0.0 or shares_outstanding <= 0.0:
        raise ValueError("adv_shares and shares_outstanding must both be > 0")
    turnover_ratio = shares_outstanding / adv_shares
    return (coefficients.permanent_gamma
            * volatility_daily
            * (order_qty / adv_shares)
            * turnover_ratio ** coefficients.liquidity_delta)


def athl_temporary_impact_fraction(
    volatility_daily: float,
    participation_rate: float,
    coefficients: Optional[ImpactModelCoefficients] = None,
) -> float:
    """
    Temporary impact K as a fraction of the arrival price (ATHL 2005, Sec. 4.3):

        K = eta * sigma * (X / (V * T)) ** beta

    where X/(V*T) is the participation rate -- shares traded per unit time relative
    to average daily volume. beta = 0.600, NOT the square root: ATHL reject beta = 1/2
    at the 95% confidence level (ibid. Sec. 4.2).

    Reproduces their Table 3: sigma = 1.57% at participation 1.0 / 0.5 / 0.2 gives
    22.3 / 14.7 / 8.5 bp against the 22 / 15 / 8 bp printed in the paper.
    """
    coefficients = coefficients or ImpactModelCoefficients()
    if participation_rate < 0.0:
        raise ValueError(f"participation_rate must be >= 0, got {participation_rate!r}")
    if participation_rate == 0.0:
        return 0.0
    return coefficients.temporary_eta * volatility_daily * participation_rate ** coefficients.temporary_beta


def almgren_chriss_kappa_t(
    config: AlgoParameterConfig,
    sample: HistoricalTradeSample,
    coefficients: Optional[ImpactModelCoefficients] = None,
) -> float:
    """
    Dimensionless Almgren-Chriss decay kappa*T for this (config, sample) pair.

    AC Eq. (19) gives kappa ~ sqrt(lambda * sigma^2 / eta) as the trading interval
    shrinks, with sigma the absolute volatility in $/share and eta the *linear*
    temporary-impact coefficient in ($/share)/(share/day). This module obtains eta by
    linearising the ATHL temporary-impact cost at the participation ceiling:

        eta_linear = P * K(alpha) / (alpha * V)      with K(alpha) the ATHL fraction

    That linearisation is an approximation -- ATHL's cost function carries sigma, so
    its units differ from AC's linear eta by a day^(1/2) factor that is unity only at
    a one-day reference horizon. lambda is therefore meaningful as an *ordering* over
    the grid on this module's scale, not as a transferable physical constant.

    Returns 0.0 for the risk-neutral limit (lambda = 0 or zero volatility), where the
    trajectory degenerates to linear/TWAP.
    """
    coefficients = coefficients or ImpactModelCoefficients()
    alpha = config.max_participation_rate
    sigma_daily = sample.volatility_daily_pct
    if config.risk_aversion_lambda <= 0.0 or sigma_daily <= 0.0:
        return 0.0

    impact_fraction_at_ceiling = athl_temporary_impact_fraction(sigma_daily, alpha, coefficients)
    if impact_fraction_at_ceiling <= 0.0:
        # No modelled impact means nothing restrains urgency; front-load completely.
        return math.inf

    eta_linear = (sample.arrival_price * impact_fraction_at_ceiling) / (alpha * sample.market_adv_shares)
    sigma_cash_squared = (sigma_daily * sample.arrival_price) ** 2
    kappa = math.sqrt(config.risk_aversion_lambda * sigma_cash_squared / eta_linear)
    return kappa * sample.execution_horizon_days


def _ac_inventory_fraction(kappa_t: float, time_remaining_fraction: float) -> float:
    """
    AC Eq. (17) as a fraction of the parent order still to be executed:

        x_j / X = sinh(kappa * (T - t_j)) / sinh(kappa * T)

    ``time_remaining_fraction`` is (T - t_j)/T in [0, 1]. Evaluated through expm1 so
    that a large kappa*T -- where sinh overflows a float -- stays exact, and falling
    back to the linear limit where sinh(0)/sinh(0) is undefined.
    """
    if time_remaining_fraction <= 0.0:
        return 0.0
    if time_remaining_fraction >= 1.0:
        return 1.0
    if kappa_t < _KAPPA_T_LINEAR_LIMIT:
        return time_remaining_fraction          # risk-neutral limit: linear / TWAP
    if math.isinf(kappa_t):
        return 0.0                              # infinite urgency: everything in interval 1
    a = kappa_t * time_remaining_fraction
    # exp(a - kappa_t) never overflows because a <= kappa_t; -expm1(-2x) is 1 - e^(-2x).
    return math.exp(a - kappa_t) * (-math.expm1(-2.0 * a)) / (-math.expm1(-2.0 * kappa_t))


class ExecutionAlgoOptimizerEngine:
    """
    Grid-search optimizer for execution-algorithm parameters, scored by Implementation
    Shortfall replayed over historical intraday price paths.

    The objective is the mean-variance utility of Almgren & Chriss (2000) plus an
    incomplete-fill penalty:

        Score(theta) = mean(IS) + w_vol * stdev(IS) + (1 - mean_fill) * w_fill

    Lower is better. ``w_fill`` is expressed in basis points of shortfall per unit of
    unfilled fraction, so it is a *policy* statement about how badly you want the
    order finished -- it is not derived from market data. The default of 100.0 means
    leaving 10% of the order unexecuted is charged the same as 10 bp of shortfall.
    """

    def __init__(
        self,
        is_volatility_penalty_weight: float = 0.5,
        incomplete_fill_penalty_weight: float = 100.0,
        max_allowed_participation_rate: float = DEFAULT_MAX_ALLOWED_PARTICIPATION_RATE,
        impact_coefficients: Optional[ImpactModelCoefficients] = None,
        overfit_degradation_threshold_bps: float = DEFAULT_OVERFIT_DEGRADATION_BPS,
    ) -> None:
        if not _is_finite(is_volatility_penalty_weight) or is_volatility_penalty_weight < 0.0:
            raise ValueError("is_volatility_penalty_weight must be finite and >= 0")
        if not _is_finite(incomplete_fill_penalty_weight) or incomplete_fill_penalty_weight < 0.0:
            raise ValueError("incomplete_fill_penalty_weight must be finite and >= 0")
        if not _is_finite(max_allowed_participation_rate) or not 0.0 < max_allowed_participation_rate <= 1.0:
            raise ValueError("max_allowed_participation_rate must be finite in (0, 1]")
        if not _is_finite(overfit_degradation_threshold_bps) or overfit_degradation_threshold_bps < 0.0:
            raise ValueError("overfit_degradation_threshold_bps must be finite and >= 0")

        self.is_volatility_penalty_weight = is_volatility_penalty_weight
        self.incomplete_fill_penalty_weight = incomplete_fill_penalty_weight
        self.max_allowed_participation_rate = max_allowed_participation_rate
        self.impact_coefficients = impact_coefficients or ImpactModelCoefficients()
        self.overfit_degradation_threshold_bps = overfit_degradation_threshold_bps

    def simulate_single_execution(
        self,
        config: AlgoParameterConfig,
        sample: HistoricalTradeSample,
    ) -> ExecutionSimulationResult:
        """
        Replay one parent order over its historical price path under ``config``.

        The Almgren-Chriss trajectory sets the target inventory per interval; the
        participation ceiling caps what that interval can actually absorb, and any
        shortfall against the target rolls forward. Nothing is assumed filled that the
        observed volume could not have absorbed at the configured ceiling.
        """
        prices = sample.historical_execution_prices
        volumes = sample.interval_volumes or []
        n_intervals = len(prices)
        arrival = sample.arrival_price
        sign = sample.side_sign
        order_qty = float(sample.order_qty)

        kappa_t = almgren_chriss_kappa_t(config, sample, self.impact_coefficients)

        permanent_fraction = 0.0
        permanent_applied = sample.shares_outstanding is not None
        if permanent_applied:
            permanent_fraction = athl_permanent_impact_fraction(
                sample.volatility_daily_pct,
                order_qty,
                sample.market_adv_shares,
                float(sample.shares_outstanding),
                self.impact_coefficients,
            )

        peg_cost_per_share = config.peg_offset_ticks * sample.tick_size

        filled_qty = 0.0
        notional = 0.0
        pending_target = 0.0
        previous_inventory = order_qty

        for j in range(1, n_intervals + 1):
            # AC Eq. (17): inventory remaining at the end of interval j.
            time_remaining_fraction = (n_intervals - j) / n_intervals
            target_inventory = order_qty * _ac_inventory_fraction(kappa_t, time_remaining_fraction)
            pending_target += max(0.0, previous_inventory - target_inventory)
            previous_inventory = target_inventory

            remaining = order_qty - filled_qty
            if remaining <= 0.0:
                break

            capacity = config.max_participation_rate * volumes[j - 1]
            slice_qty = min(pending_target, capacity, remaining)
            if slice_qty <= 0.0:
                continue
            pending_target -= slice_qty

            participation = slice_qty / volumes[j - 1] if volumes[j - 1] > 0.0 else 0.0
            temporary_fraction = athl_temporary_impact_fraction(
                sample.volatility_daily_pct, participation, self.impact_coefficients)

            # Permanent impact accumulates with what we have already traded; the
            # midpoint of this slice is the right point to value the slice at.
            executed_fraction_midpoint = (filled_qty + slice_qty / 2.0) / order_qty
            displacement = permanent_fraction * executed_fraction_midpoint

            execution_price = prices[j - 1] * (1.0 + sign * (displacement + temporary_fraction))
            execution_price += sign * peg_cost_per_share

            filled_qty += slice_qty
            notional += slice_qty * execution_price

        fill_rate = filled_qty / order_qty
        achieved_vwap = notional / filled_qty if filled_qty > 0.0 else 0.0

        execution_cost_bps = (
            sign * (achieved_vwap - arrival) / arrival * 1e4 if filled_qty > 0.0 else 0.0)
        # Perold (1988): the unexecuted quantity is not free -- it is charged the move
        # from the decision price to where the market ended up without us.
        opportunity_cost_bps = sign * (prices[-1] - arrival) / arrival * 1e4

        shortfall_bps = fill_rate * execution_cost_bps + (1.0 - fill_rate) * opportunity_cost_bps

        return ExecutionSimulationResult(
            trade_id=sample.trade_id,
            implementation_shortfall_bps=shortfall_bps,
            fill_completion_rate=fill_rate,
            execution_cost_bps=execution_cost_bps,
            opportunity_cost_bps=opportunity_cost_bps,
            achieved_vwap=achieved_vwap,
            filled_qty=filled_qty,
            intervals_simulated=n_intervals,
            kappa_t=kappa_t,
            permanent_impact_applied=permanent_applied,
        )

    def evaluate_candidate(
        self,
        config: AlgoParameterConfig,
        trade_samples: Sequence[HistoricalTradeSample],
    ) -> OptimizationCandidateResult:
        """Replay one config across every sample and score it. Raises on a non-finite score."""
        shortfalls: List[float] = []
        fills: List[float] = []

        for sample in trade_samples:
            result = self.simulate_single_execution(config, sample)
            if not _is_finite(result.implementation_shortfall_bps):
                raise ValueError(
                    f"non-finite Implementation Shortfall for {sample.trade_id} under "
                    f"{config.describe()}; check the sample's price path and volatility")
            shortfalls.append(result.implementation_shortfall_bps)
            fills.append(result.fill_completion_rate)

        mean_is = statistics.fmean(shortfalls)
        std_is = statistics.stdev(shortfalls) if len(shortfalls) > 1 else 0.0
        avg_fill = statistics.fmean(fills)
        score = (mean_is
                 + self.is_volatility_penalty_weight * std_is
                 + (1.0 - avg_fill) * self.incomplete_fill_penalty_weight)

        if not _is_finite(score):
            raise ValueError(f"non-finite utility score for {config.describe()}")

        return OptimizationCandidateResult(
            config=config,
            mean_implementation_shortfall_bps=round(mean_is, 4),
            std_implementation_shortfall_bps=round(std_is, 4),
            avg_fill_completion_rate=round(avg_fill, 6),
            utility_score=round(score, 4),
            worst_implementation_shortfall_bps=round(max(shortfalls), 4),
            min_fill_completion_rate=round(min(fills), 6),
            samples_evaluated=len(shortfalls),
            mean_is_standard_error_bps=round(std_is / math.sqrt(len(shortfalls)), 4),
        )

    def optimize_algo_parameters(
        self,
        algo_name: str,
        symbol: str,
        grid_search_configs: Sequence[AlgoParameterConfig],
        trade_samples: Sequence[HistoricalTradeSample],
        holdout_samples: Optional[Sequence[HistoricalTradeSample]] = None,
    ) -> AlgoOptimizationAuditReport:
        """
        Score every eligible grid candidate on ``trade_samples`` and select the lowest
        utility score, then -- if ``holdout_samples`` is supplied -- re-score the winner
        out of sample and report the degradation.

        Candidates whose participation ceiling exceeds ``max_allowed_participation_rate``
        are never eligible for selection; they are recorded in ``rejected_configs`` with
        the reason rather than dropped silently. If that leaves nothing to evaluate, the
        call raises instead of returning an arbitrary answer.

        Ties in utility score resolve to the earliest candidate in the supplied grid
        order, so the same inputs always select the same configuration.
        """
        if not grid_search_configs:
            raise ValueError("grid_search_configs cannot be empty.")
        if not trade_samples:
            raise ValueError("trade_samples cannot be empty.")

        warnings: List[str] = []
        rejected: List[Tuple[AlgoParameterConfig, str]] = []
        eligible: List[AlgoParameterConfig] = []

        for config in grid_search_configs:
            if config.max_participation_rate > self.max_allowed_participation_rate:
                reason = (f"participation ceiling {config.max_participation_rate:.2%} exceeds the "
                          f"configured limit of {self.max_allowed_participation_rate:.2%}")
                rejected.append((config, reason))
                logger.warning("Candidate excluded (%s): %s", config.describe(), reason)
            else:
                eligible.append(config)

        if not eligible:
            raise ValueError(
                f"every one of the {len(grid_search_configs)} grid candidates exceeds the "
                f"{self.max_allowed_participation_rate:.2%} participation limit; widen the limit "
                f"deliberately or lower the grid.")

        results = [self.evaluate_candidate(config, trade_samples) for config in eligible]
        # sorted() is stable, so equal scores keep the caller's grid order.
        sorted_results = sorted(results, key=lambda r: r.utility_score)
        optimal = sorted_results[0]

        # Is the winner actually distinguishable from the runner-up, or is the ranking
        # inside the sampling noise? Compared against the combined standard error of the
        # two candidates' mean IS -- a first-order check on the dominant term of the
        # score, not a formal hypothesis test.
        selection_margin: Optional[float] = None
        selection_separated: Optional[bool] = None
        if len(sorted_results) > 1:
            runner_up = sorted_results[1]
            selection_margin = round(runner_up.utility_score - optimal.utility_score, 4)
            combined_standard_error = math.hypot(
                optimal.mean_is_standard_error_bps, runner_up.mean_is_standard_error_bps)
            selection_separated = selection_margin > combined_standard_error
            if not selection_separated:
                warnings.append(
                    f"SELECTION NOT SEPARATED FROM NOISE: the winner beats the runner-up by "
                    f"{selection_margin:.2f} score points, inside the {combined_standard_error:.2f} bps "
                    f"combined standard error on their mean shortfalls. With {optimal.samples_evaluated} "
                    f"samples this ranking is not evidence that one configuration is better; add samples "
                    f"or treat the top candidates as equivalent.")

        holdout_mean_is: Optional[float] = None
        holdout_fill: Optional[float] = None
        degradation: Optional[float] = None

        if holdout_samples:
            holdout_result = self.evaluate_candidate(optimal.config, holdout_samples)
            holdout_mean_is = holdout_result.mean_implementation_shortfall_bps
            holdout_fill = holdout_result.avg_fill_completion_rate
            degradation = round(holdout_mean_is - optimal.mean_implementation_shortfall_bps, 4)
            if degradation > self.overfit_degradation_threshold_bps:
                warnings.append(
                    f"OUT-OF-SAMPLE DEGRADATION: holdout mean IS is {degradation:.2f} bps worse than "
                    f"in-sample (threshold {self.overfit_degradation_threshold_bps:.2f} bps). Treat the "
                    f"selected configuration as overfitted to the in-sample period.")
        else:
            warnings.append(
                "NO HOLDOUT SUPPLIED: the selected configuration is in-sample only and has not been "
                "validated out of sample. Do not promote it to production on this result alone.")

        if rejected:
            warnings.append(
                f"{len(rejected)} of {len(grid_search_configs)} candidates were excluded by the "
                f"{self.max_allowed_participation_rate:.2%} participation limit.")

        if not any(s.shares_outstanding is not None for s in trade_samples):
            warnings.append(
                "PERMANENT IMPACT OMITTED: no sample supplied shares_outstanding, so the ATHL "
                "permanent-impact term was skipped and total cost is understated.")

        notes = (
            f"ALGO PARAMETER OPTIMIZATION COMPLETE [{algo_name} - {symbol}]: evaluated {len(eligible)} of "
            f"{len(grid_search_configs)} candidates across {len(trade_samples)} samples. "
            f"Optimal: {optimal.config.describe()} -> utility={optimal.utility_score:.2f} "
            f"(mean IS={optimal.mean_implementation_shortfall_bps:.2f}bps, "
            f"worst IS={optimal.worst_implementation_shortfall_bps:.2f}bps, "
            f"fill={optimal.avg_fill_completion_rate * 100:.1f}%)."
        )
        if selection_margin is not None:
            notes += (f" Margin over runner-up={selection_margin:.2f} "
                      f"({'separated' if selection_separated else 'INSIDE NOISE'}).")
        if degradation is not None:
            notes += f" Holdout mean IS={holdout_mean_is:.2f}bps (degradation {degradation:+.2f}bps)."
        logger.info(notes)
        for warning in warnings:
            logger.warning("OPTIMIZATION WARNING [%s - %s]: %s", algo_name, symbol, warning)

        return AlgoOptimizationAuditReport(
            algo_name=algo_name,
            symbol=symbol,
            total_trade_samples_tested=len(trade_samples),
            total_grid_candidates_evaluated=len(eligible),
            optimal_config=optimal.config,
            optimal_utility_score=optimal.utility_score,
            optimal_mean_is_bps=optimal.mean_implementation_shortfall_bps,
            optimal_std_is_bps=optimal.std_implementation_shortfall_bps,
            all_candidate_results=sorted_results,
            audit_notes=notes,
            rejected_configs=rejected,
            holdout_evaluated=bool(holdout_samples),
            holdout_mean_is_bps=holdout_mean_is,
            holdout_fill_completion_rate=holdout_fill,
            holdout_is_degradation_bps=degradation,
            selection_margin_score=selection_margin,
            selection_is_separated=selection_separated,
            warnings=warnings,
        )
