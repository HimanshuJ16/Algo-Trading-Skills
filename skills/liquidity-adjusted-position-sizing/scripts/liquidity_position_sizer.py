"""
liquidity-adjusted-position-sizing: ADV liquidity cap calculator,
Days-to-Liquidate (DTL) auditor, and position size scaling engine.
"""
from dataclasses import dataclass
import logging
import math
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class LiquiditySizingResult:
    symbol: str
    target_capital_usd: float
    target_shares: float
    adv_shares_20d: float
    price: float
    max_participation_pct: float
    dtl_days: float
    liquidity_capped_shares: float
    liquidity_capped_capital_usd: float
    scaling_factor: float      # 1.0 = no cap, <1.0 = scaled down
    is_liquidity_constrained: bool
    message: str


class LiquidityPositionSizer:
    """
    Sizes portfolio positions relative to instrument ADV and Days-To-Liquidate (DTL)
    limits to prevent market impact and liquidity lockup.
    """

    def __init__(
        self,
        max_participation_pct: float = 10.0,  # Max 10% of ADV
        max_dtl_days: float = 1.0,            # Max 1 day to liquidate
    ):
        self.max_participation_pct = max_participation_pct
        self.max_dtl_days = max_dtl_days

    def calculate_size(
        self,
        symbol: str,
        target_capital_usd: float,
        price: float,
        adv_shares_20d: float,
    ) -> LiquiditySizingResult:
        if price <= 0 or adv_shares_20d <= 0:
            raise ValueError(f"Invalid price ({price}) or ADV ({adv_shares_20d}) for {symbol}.")

        raw_target_shares = target_capital_usd / price

        # Daily allowable liquidation capacity
        daily_capacity_shares = (self.max_participation_pct / 100.0) * adv_shares_20d

        # Max allowed position shares based on DTL limit
        max_allowed_shares = daily_capacity_shares * self.max_dtl_days

        dtl_raw = raw_target_shares / max(0.001, daily_capacity_shares)

        if raw_target_shares > max_allowed_shares:
            final_shares = max_allowed_shares
            final_capital = final_shares * price
            scaling_factor = final_shares / raw_target_shares
            is_constrained = True
            msg = (
                f"LIQUIDITY CAP APPLIED for '{symbol}': Target ${target_capital_usd:,.2f} scaled down to "
                f"${final_capital:,.2f} ({scaling_factor * 100:.1f}%) to enforce DTL <= {self.max_dtl_days}d."
            )
            logger.warning(msg)
        else:
            final_shares = raw_target_shares
            final_capital = target_capital_usd
            scaling_factor = 1.0
            is_constrained = False
            msg = f"Liquidity Sizing OK for '{symbol}': DTL={dtl_raw:.2f} days <= {self.max_dtl_days}d."
            logger.info(msg)

        return LiquiditySizingResult(
            symbol=symbol,
            target_capital_usd=round(target_capital_usd, 2),
            target_shares=round(raw_target_shares, 2),
            adv_shares_20d=round(adv_shares_20d, 2),
            price=round(price, 2),
            max_participation_pct=self.max_participation_pct,
            dtl_days=round(dtl_raw, 2),
            liquidity_capped_shares=round(final_shares, 2),
            liquidity_capped_capital_usd=round(final_capital, 2),
            scaling_factor=round(scaling_factor, 4),
            is_liquidity_constrained=is_constrained,
            message=msg,
        )
