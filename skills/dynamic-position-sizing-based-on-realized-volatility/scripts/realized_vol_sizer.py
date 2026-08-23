"""
dynamic-position-sizing-based-on-realized-volatility: volatility-targeting position
sizer supporting Rolling StdDev and RiskMetrics EWMA estimators.

Scales capital inversely to realized volatility so the position carries a constant
*ex-ante* risk budget:

    scalar = target_vol / max(vol_floor, realized_vol)
    scalar = clip(scalar, min_scalar, max_scalar)
    capital = base_capital * scalar

EWMA estimator (RiskMetrics Technical Document, 4th ed. 1996, Eq. 5.3 / 5.37):

    sigma_t^2 = lambda * sigma_{t-1}^2 + (1 - lambda) * r_{t-1}^2

with lambda = 0.94 for daily data and 0.97 for monthly (ibid. Sec. 5.3.2). Squared
returns are used raw: RiskMetrics centres returns on zero rather than on the sample
mean (ibid. Sec. 5.3.1.2). The rolling estimator instead uses the mean-subtracted
sample standard deviation (n-1), so the two methods embed *different* mean
assumptions and will not agree on the same data; pick one per strategy and stay with
it rather than comparing their outputs.

Minimum history is derived from RiskMetrics Eq. 5.26, K = ln(tolerance)/ln(lambda) —
the effective number of observations an EWMA actually consumes. At lambda = 0.94 and
a 1% tolerance that is 74 daily returns; supplying fewer means the arbitrary seed
still carries more than 1% of the weight, so the "realized volatility" is largely an
artifact of the first observation.

Limitations (documented, deliberate):

- **Backward-looking.** The scalar is set from volatility already realized. It cannot
  anticipate a gap or a jump, and it stabilises the *forecast* risk budget, not the
  outcome. It is not a stop-loss and not a drawdown control.
- **Annualization must match sampling frequency.** ``annualization_factor`` is the
  number of return observations per year (252 for daily bars). Feeding intraday bars
  while leaving it at 252 understates volatility by roughly sqrt(bars per day) and
  therefore oversizes the position. The frequency cannot be inferred from the data;
  the caller must set it.
- **Look-ahead is the caller's responsibility.** ``returns_history`` must end at the
  last *completed* observation before the bar being sized. RiskMetrics Eq. 5.37 sizes
  period t from r_{t-1}; including the current bar's return leaks the outcome into
  the size.
- **Single-asset.** No correlation or portfolio-level aggregation: summing
  independently vol-targeted positions does not produce a vol-targeted portfolio.
- **Lot/tick rounding is out of scope**; ``target_shares`` is floored, never rounded
  up, so it can never exceed the risk budget.
"""
import logging
import math
from dataclasses import dataclass
from typing import List, Sequence

logger = logging.getLogger(__name__)

#: RiskMetrics daily decay factor (Technical Document 4th ed., Sec. 5.3.2).
RISKMETRICS_DAILY_DECAY = 0.94

#: RiskMetrics monthly decay factor (ibid.).
RISKMETRICS_MONTHLY_DECAY = 0.97

#: Default tolerance for the effective-observation calculation (RiskMetrics Table 5.7
#: tabulates 0.001%, 0.01%, 0.1% and 1%; 1% is the loosest published level).
DEFAULT_EWMA_TOLERANCE = 0.01

#: Scalar deadband for regime labelling. Within +/-5% of 1.0 the position is treated
#: as "at target" so routine estimator noise is not reported as a regime change.
LEVERAGE_DEADBAND_UPPER = 1.05
LEVERAGE_DEADBAND_LOWER = 0.95


@dataclass
class VolatilityTargetingResult:
    symbol: str
    base_capital_usd: float
    target_annualized_vol: float
    realized_annualized_vol: float      # As estimated. NOT floored — see vol_used_for_scaling.
    raw_vol_scalar: float
    bounded_vol_scalar: float
    adjusted_capital_usd: float
    target_shares: float
    estimation_method: str
    is_leveraged_up: bool
    is_scaled_down: bool
    status_message: str
    vol_used_for_scaling: float = 0.0   # max(vol_floor, realized) — the scalar denominator
    vol_floor_binding: bool = False     # True when the floor, not realized vol, set the size
    observations_used: int = 0


def required_ewma_observations(
    decay_factor: float = RISKMETRICS_DAILY_DECAY,
    tolerance: float = DEFAULT_EWMA_TOLERANCE,
) -> int:
    """
    Effective number of observations an EWMA consumes, per RiskMetrics Eq. 5.26:

        K = ln(tolerance) / ln(decay_factor)

    Reproduces the published Table 5.7 exactly (lambda 0.94 -> 74 observations at a 1%
    tolerance, 112 at 0.1%, 149 at 0.01%, 186 at 0.001%; lambda 0.97 -> 151 at 1%).
    Below K observations the seed variance still carries more than ``tolerance`` of the
    total weight, so the estimate reflects the seed as much as the data.
    """
    if not 0.0 < decay_factor < 1.0:
        raise ValueError(f"decay_factor must be in (0, 1), got {decay_factor}.")
    if not 0.0 < tolerance < 1.0:
        raise ValueError(f"tolerance must be in (0, 1), got {tolerance}.")
    # Rounded, not truncated or ceiled: that is what reproduces Table 5.7 exactly
    # (lambda 0.94 -> 74.44 -> 74, but lambda 0.96 -> 112.8 -> 113).
    return math.floor(math.log(tolerance) / math.log(decay_factor) + 0.5)


def _validate_returns(returns: Sequence[float], minimum: int, context: str) -> List[float]:
    """
    Rejects non-finite returns and histories too short to support an estimate.

    A single NaN previously propagated to a NaN variance, which ``math.sqrt(max(0.0, nan))``
    silently collapsed to 0.0 volatility — floored to ``vol_floor``, producing the
    *maximum* leverage scalar on corrupt data. Sizing must fail loudly instead.
    """
    if returns is None:
        raise ValueError(f"{context}: returns_history is required, got None.")
    values = list(returns)
    for i, r in enumerate(values):
        if isinstance(r, bool) or not isinstance(r, (int, float)):
            raise TypeError(f"{context}: return at index {i} must be numeric, got {type(r).__name__}.")
        if not math.isfinite(r):
            raise ValueError(
                f"{context}: return at index {i} is non-finite ({r}). A non-finite return "
                "collapses the volatility estimate to the floor and yields maximum leverage."
            )
    if len(values) < minimum:
        raise ValueError(
            f"{context}: insufficient return history — got {len(values)} observations, "
            f"require at least {minimum}. Sizing on a shorter history reports an estimate "
            "dominated by the seed rather than by realized volatility."
        )
    return values


class RealizedVolPositionSizer:
    """
    Dynamically scales position size inversely to recent realized volatility
    to maintain a constant target risk budget across market regimes.
    """

    def __init__(
        self,
        target_annualized_vol: float = 0.15,  # 15% target annual volatility
        min_scalar: float = 0.20,              # Max scale down (20% of base)
        max_scalar: float = 2.00,              # Max leverage up (200% of base)
        vol_floor: float = 0.05,               # Min annual vol used as scalar denominator
        annualization_factor: float = 252.0,   # Return observations per year (252 = daily)
        min_rolling_observations: int = 20,    # Minimum sample for the rolling estimator
        ewma_tolerance: float = DEFAULT_EWMA_TOLERANCE,
    ):
        if not target_annualized_vol > 0:
            raise ValueError(f"target_annualized_vol must be > 0, got {target_annualized_vol}.")
        if not vol_floor > 0:
            raise ValueError(
                f"vol_floor must be > 0, got {vol_floor}. It is the scalar denominator; "
                "a zero floor permits unbounded leverage on a quiet series."
            )
        if not 0 <= min_scalar <= max_scalar:
            raise ValueError(
                f"require 0 <= min_scalar <= max_scalar, got min={min_scalar}, max={max_scalar}."
            )
        if not annualization_factor > 0 or not math.isfinite(annualization_factor):
            raise ValueError(
                f"annualization_factor must be a positive finite number, got {annualization_factor}. "
                "It is observations per year: 252 for daily bars, 252*78 for 5-minute bars."
            )
        if min_rolling_observations < 2:
            raise ValueError(
                f"min_rolling_observations must be >= 2 (a sample variance needs two "
                f"observations), got {min_rolling_observations}."
            )

        self.target_vol = target_annualized_vol
        self.min_scalar = min_scalar
        self.max_scalar = max_scalar
        self.vol_floor = vol_floor
        self.ann_factor = annualization_factor
        self.min_rolling_observations = min_rolling_observations
        self.ewma_tolerance = ewma_tolerance

        if self.target_vol / self.vol_floor < self.max_scalar:
            # Not an error, but the floor can then never bind before the cap does.
            logger.info(
                "vol_floor %.4f implies a maximum raw scalar of %.2f, below max_scalar %.2f: "
                "the floor will never bind before the cap.",
                self.vol_floor, self.target_vol / self.vol_floor, self.max_scalar,
            )

    def compute_rolling_volatility(self, returns: List[float]) -> float:
        """
        Annualized sample standard deviation (n-1, mean-subtracted).

        Returns the estimate **unfloored** — the floor is a sizing guard applied to the
        scalar denominator, not a property of realized volatility. Reporting the floored
        value as "realized volatility" overstated genuinely quiet series by a wide margin.
        """
        values = _validate_returns(returns, self.min_rolling_observations, "compute_rolling_volatility")
        n = len(values)
        mean_r = sum(values) / float(n)
        var = sum((r - mean_r) ** 2 for r in values) / float(n - 1)
        return math.sqrt(max(0.0, var)) * math.sqrt(self.ann_factor)

    def compute_ewma_volatility(
        self,
        returns: List[float],
        decay_factor: float = RISKMETRICS_DAILY_DECAY,
    ) -> float:
        """
        Annualized RiskMetrics EWMA volatility, seeded with the first squared return and
        recursed as sigma^2 = lambda*sigma^2 + (1-lambda)*r^2 (Eq. 5.3).

        Requires at least ``required_ewma_observations(decay_factor, tolerance)`` returns
        so the seed's residual weight is within tolerance. Returns the estimate
        **unfloored**.
        """
        minimum = required_ewma_observations(decay_factor, self.ewma_tolerance)
        values = _validate_returns(returns, minimum, "compute_ewma_volatility")

        variance = values[0] ** 2
        for r in values[1:]:
            variance = decay_factor * variance + (1.0 - decay_factor) * (r ** 2)

        return math.sqrt(max(0.0, variance)) * math.sqrt(self.ann_factor)

    def calculate_position_size(
        self,
        symbol: str,
        base_capital_usd: float,
        price: float,
        returns_history: List[float],
        use_ewma: bool = True,
        decay_factor: float = RISKMETRICS_DAILY_DECAY,
    ) -> VolatilityTargetingResult:
        """
        Sizes a position against the target volatility budget.

        ``returns_history`` must end at the last completed observation *before* the bar
        being sized, and must be sampled at the frequency implied by
        ``annualization_factor``. Raises rather than sizing when the history is
        non-finite or too short: a silent fallback previously returned a scalar of
        exactly 1.0, i.e. a full-size position justified by no data at all.
        """
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError(f"symbol must be a non-empty string, got {symbol!r}.")
        if not math.isfinite(price) or price <= 0:
            raise ValueError(f"Invalid price {price} for symbol '{symbol}'.")
        if not math.isfinite(base_capital_usd) or base_capital_usd < 0:
            raise ValueError(
                f"base_capital_usd must be a non-negative finite number for '{symbol}', "
                f"got {base_capital_usd}. A negative base silently inverted the position."
            )

        if use_ewma:
            realized_vol = self.compute_ewma_volatility(returns_history, decay_factor)
            method = "EWMA_RiskMetrics"
        else:
            realized_vol = self.compute_rolling_volatility(returns_history)
            method = "Rolling_StdDev"

        if realized_vol <= 0.0:
            # Exactly zero variance means every return was identical — the signature of a
            # stale feed, a halted instrument, or a loading bug, not of a quiet market.
            # Left unchecked the floor turns it into the MAXIMUM leverage scalar, i.e. the
            # largest possible position from the most obviously broken input.
            raise ValueError(
                f"Zero realized volatility for '{symbol}' over {len(returns_history)} "
                "observations: the return series is constant, which indicates a stale or "
                "halted feed rather than a quiet market. Refusing to size."
            )

        vol_for_scaling = max(self.vol_floor, realized_vol)
        floor_binding = realized_vol < self.vol_floor

        raw_scalar = self.target_vol / vol_for_scaling
        bounded_scalar = max(self.min_scalar, min(self.max_scalar, raw_scalar))

        adjusted_capital = base_capital_usd * bounded_scalar
        # Floor, never round up: rounding up would place a position above the risk budget.
        target_shares = math.floor((adjusted_capital / price) * 100.0) / 100.0

        is_leveraged = bounded_scalar > LEVERAGE_DEADBAND_UPPER
        is_scaled_down = bounded_scalar < LEVERAGE_DEADBAND_LOWER

        if is_scaled_down:
            msg = (
                f"HIGH VOL REGIME for '{symbol}': Realized Vol={realized_vol * 100:.1f}% > Target {self.target_vol * 100:.1f}%. "
                f"Scaled down position to {bounded_scalar * 100:.1f}% (${adjusted_capital:,.2f})."
            )
            logger.warning(msg)
        elif is_leveraged:
            msg = (
                f"QUIET VOL REGIME for '{symbol}': Realized Vol={realized_vol * 100:.1f}% < Target {self.target_vol * 100:.1f}%. "
                f"Leveraged up position to {bounded_scalar * 100:.1f}% (${adjusted_capital:,.2f})."
            )
            logger.info(msg)
        else:
            msg = f"VOL OK for '{symbol}': Realized Vol={realized_vol * 100:.1f}% ~ Target {self.target_vol * 100:.1f}%."

        if floor_binding:
            logger.warning(
                "VOL FLOOR BINDING for '%s': realized vol %.4f below floor %.4f; size set by "
                "the floor, not by realized volatility.", symbol, realized_vol, self.vol_floor,
            )

        return VolatilityTargetingResult(
            symbol=symbol,
            base_capital_usd=round(base_capital_usd, 2),
            target_annualized_vol=round(self.target_vol, 4),
            realized_annualized_vol=round(realized_vol, 4),
            raw_vol_scalar=round(raw_scalar, 4),
            bounded_vol_scalar=round(bounded_scalar, 4),
            adjusted_capital_usd=round(adjusted_capital, 2),
            target_shares=target_shares,
            estimation_method=method,
            is_leveraged_up=is_leveraged,
            is_scaled_down=is_scaled_down,
            status_message=msg,
            vol_used_for_scaling=round(vol_for_scaling, 4),
            vol_floor_binding=floor_binding,
            observations_used=len(returns_history),
        )
