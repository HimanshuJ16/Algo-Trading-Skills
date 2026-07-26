import math
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class OrderCheckResult:
    symbol: str
    proposed_quantity: int
    approved_quantity: int
    is_downsized: bool
    is_rejected: bool
    nav_limit_breached: bool
    adv_limit_breached: bool
    reason: str

class SingleNameConcentrationLimiter:
    """
    Pre-trade risk engine for enforcing single-name % NAV limits, % ADV limits,
    order downsizing, and portfolio Herfindahl-Hirschman Index (HHI) analysis.
    """
    def __init__(
        self,
        max_nav_pct: float = 0.05,        # Max 5% of NAV in a single name
        max_adv_pct: float = 0.10,        # Max 10% of 20-day ADV
        allow_downsizing: bool = True
    ):
        self.max_nav_pct = max_nav_pct
        self.max_adv_pct = max_adv_pct
        self.allow_downsizing = allow_downsizing

    def evaluate_order(
        self,
        symbol: str,
        side: str,                       # 'BUY' or 'SELL'
        proposed_quantity: int,
        price: float,
        portfolio_nav: float,
        current_position_value: float,
        twenty_day_adv: int
    ) -> OrderCheckResult:
        """
        Evaluates proposed order against NAV % and ADV % limits. Downsizes if necessary.
        """
        if portfolio_nav <= 0 or price <= 0:
            raise ValueError("Portfolio NAV and order price must be positive.")

        if proposed_quantity <= 0:
            return OrderCheckResult(
                symbol=symbol, proposed_quantity=0, approved_quantity=0,
                is_downsized=False, is_rejected=True, nav_limit_breached=False,
                adv_limit_breached=False, reason="Invalid proposed quantity."
            )

        side_upper = side.upper()
        
        # 1. ADV Limit Check (Applies to both BUY and SELL)
        max_adv_shares = int(twenty_day_adv * self.max_adv_pct)
        
        # 2. NAV Limit Check (Only restricts increasing exposure, e.g. BUY for long positions)
        if side_upper == 'BUY':
            max_allowed_val = (portfolio_nav * self.max_nav_pct) - current_position_value
            max_nav_shares = max(0, int(max_allowed_val / price))
        else:
            # Selling reduces exposure, so NAV limit is not breached by selling
            max_nav_shares = proposed_quantity

        # Final approved limit
        max_compliant_qty = min(max_nav_shares, max_adv_shares)
        
        nav_breached = proposed_quantity > max_nav_shares
        adv_breached = proposed_quantity > max_adv_shares

        if proposed_quantity <= max_compliant_qty:
            return OrderCheckResult(
                symbol=symbol, proposed_quantity=proposed_quantity,
                approved_quantity=proposed_quantity, is_downsized=False,
                is_rejected=False, nav_limit_breached=False,
                adv_limit_breached=False, reason="Order approved in full."
            )

        # Order exceeds limits
        if self.allow_downsizing and max_compliant_qty > 0:
            logger.info(f"Order for {symbol} downsized from {proposed_quantity} to {max_compliant_qty}.")
            return OrderCheckResult(
                symbol=symbol, proposed_quantity=proposed_quantity,
                approved_quantity=max_compliant_qty, is_downsized=True,
                is_rejected=False, nav_limit_breached=nav_breached,
                adv_limit_breached=adv_breached, reason=f"Downsized to {max_compliant_qty} shares to meet risk limits."
            )
        else:
            logger.warning(f"Order for {symbol} REJECTED due to concentration risk limits.")
            return OrderCheckResult(
                symbol=symbol, proposed_quantity=proposed_quantity,
                approved_quantity=0, is_downsized=False,
                is_rejected=True, nav_limit_breached=nav_breached,
                adv_limit_breached=adv_breached, reason="Order rejected due to concentration risk limits."
            )

    @staticmethod
    def calculate_hhi(position_values: List[float]) -> Dict[str, float]:
        """
        Calculates Herfindahl-Hirschman Index (HHI) and Effective Number of Assets (N_eff).
        HHI = sum(w_i^2)
        N_eff = 1 / HHI
        """
        abs_values = [abs(v) for v in position_values if abs(v) > 0]
        total_val = sum(abs_values)
        
        if total_val == 0:
            return {"hhi": 0.0, "effective_n": 0.0}

        weights = [v / total_val for v in abs_values]
        hhi = sum(w ** 2 for w in weights)
        effective_n = 1.0 / hhi if hhi > 0 else 0.0

        return {
            "hhi": round(hhi, 4),
            "effective_n": round(effective_n, 2)
        }
