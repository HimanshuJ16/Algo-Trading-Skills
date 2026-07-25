"""
dynamic-position-sizing-based-on-realized-volatility: Production volatility-targeting
position sizer supporting Rolling StdDev & EWMA RiskMetrics estimators.
"""
from dataclasses import dataclass, field
import logging
import math
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class VolatilityTargetingResult:
    symbol: str
    base_capital_usd: float
    target_annualized_vol: float
    realized_annualized_vol: float
    raw_vol_scalar: float
    bounded_vol_scalar: float
    adjusted_capital_usd: float
    target_shares: float
    estimation_method: str
    is_leveraged_up: bool
    is_scaled_down: bool
    status_message: str


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
        vol_floor: float = 0.05,               # Min annual vol floor (5%) to prevent div by zero
        annualization_factor: float = 252.0,   # Trading days per year
    ):
        self.target_vol = target_annualized_vol
        self.min_scalar = min_scalar
        self.max_scalar = max_scalar
        self.vol_floor = vol_floor
        self.ann_factor = annualization_factor

    def compute_rolling_volatility(self, returns: List[float]) -> float:
        """Computes sample standard deviation of return series and annualizes it."""
        n = len(returns)
        if n < 2:
            return self.target_vol  # fallback

        mean_r = sum(returns) / float(n)
        var = sum((r - mean_r) ** 2 for r in returns) / float(n - 1)
        daily_std = math.sqrt(max(0.0, var))
        annualized_vol = daily_std * math.sqrt(self.ann_factor)
        return max(self.vol_floor, annualized_vol)

    def compute_ewma_volatility(self, returns: List[float], decay_factor: float = 0.94) -> float:
        """Computes RiskMetrics EWMA volatility and annualizes it."""
        if not returns:
            return self.target_vol

        variance = returns[0] ** 2
        for r in returns[1:]:
            variance = decay_factor * variance + (1.0 - decay_factor) * (r ** 2)

        daily_std = math.sqrt(max(0.0, variance))
        annualized_vol = daily_std * math.sqrt(self.ann_factor)
        return max(self.vol_floor, annualized_vol)

    def calculate_position_size(
        self,
        symbol: str,
        base_capital_usd: float,
        price: float,
        returns_history: List[float],
        use_ewma: bool = True,
    ) -> VolatilityTargetingResult:
        if price <= 0:
            raise ValueError(f"Invalid price {price} for symbol '{symbol}'.")

        if use_ewma:
            realized_vol = self.compute_ewma_volatility(returns_history)
            method = "EWMA_RiskMetrics"
        else:
            realized_vol = self.compute_rolling_volatility(returns_history)
            method = "Rolling_StdDev"

        raw_scalar = self.target_vol / max(self.vol_floor, realized_vol)
        bounded_scalar = max(self.min_scalar, min(self.max_scalar, raw_scalar))

        adjusted_capital = base_capital_usd * bounded_scalar
        target_shares = round(adjusted_capital / price, 2)

        is_leveraged = bounded_scalar > 1.05
        is_scaled_down = bounded_scalar < 0.95

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
        )
