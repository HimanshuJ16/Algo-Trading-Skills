import math
import logging
from dataclasses import dataclass
from typing import Dict, List

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

    The NAV limit is applied to the ABSOLUTE resulting exposure, so a short
    position is capped exactly like a long one. Trades that reduce absolute
    exposure are never blocked, but a de-risking trade large enough to flip the
    position through flat is still capped on the far side.

    All thresholds are risk-policy inputs supplied by the caller. This class
    hard-codes no regulatory threshold; see references/standards.md for the
    jurisdiction-specific rules the defaults are loosely modelled on.
    """
    def __init__(
        self,
        max_nav_pct: float = 0.05,        # Max 5% of NAV in a single name (absolute exposure)
        max_adv_pct: float = 0.10,        # Max 10% of the configured ADV window
        allow_downsizing: bool = True
    ):
        # Guard the classic unit error: passing 5 to mean "5%" would otherwise
        # silently install a 500%-of-NAV cap and disable the control.
        if not (math.isfinite(max_nav_pct) and 0.0 < max_nav_pct <= 1.0):
            raise ValueError(
                f"max_nav_pct must be a fraction in (0, 1]; got {max_nav_pct!r}. "
                "Use 0.05 for 5%, not 5."
            )
        if not (math.isfinite(max_adv_pct) and 0.0 < max_adv_pct <= 1.0):
            raise ValueError(
                f"max_adv_pct must be a fraction in (0, 1]; got {max_adv_pct!r}. "
                "Use 0.10 for 10%, not 10."
            )
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
        twenty_day_adv: int,
        pending_exposure_value: float = 0.0
    ) -> OrderCheckResult:
        """
        Evaluates a proposed order against the NAV % and ADV % limits, downsizing
        if necessary.

        Args:
            side: 'BUY' or 'SELL' (case-insensitive). Any other value raises;
                the limiter never guesses a side.
            current_position_value: SIGNED market value of the existing position.
                Negative for a short. Include option/single-stock-future delta
                notional here if those exposures share the single-name limit.
            twenty_day_adv: Average daily volume over the firm's configured
                lookback window, in shares. Must be positive; a non-positive or
                missing value fails the order closed rather than open.
            pending_exposure_value: SIGNED notional of orders for this symbol
                already sent to a venue but not yet filled or cancelled.
                Counting these is required to stop N concurrent orders from
                each passing individually and breaching collectively
                (MiFID II RTS 6 Art. 15(2)).

        Returns:
            OrderCheckResult with the approved quantity (0 if rejected).
        """
        if not (math.isfinite(portfolio_nav) and portfolio_nav > 0):
            raise ValueError(f"Portfolio NAV must be a positive finite number; got {portfolio_nav!r}.")
        if not (math.isfinite(price) and price > 0):
            raise ValueError(f"Order price must be a positive finite number; got {price!r}.")
        if not math.isfinite(current_position_value):
            raise ValueError(f"current_position_value must be finite; got {current_position_value!r}.")
        if not math.isfinite(pending_exposure_value):
            raise ValueError(f"pending_exposure_value must be finite; got {pending_exposure_value!r}.")

        side_upper = side.strip().upper() if isinstance(side, str) else None
        if side_upper not in ("BUY", "SELL"):
            raise ValueError(f"side must be 'BUY' or 'SELL'; got {side!r}.")

        if proposed_quantity <= 0:
            return OrderCheckResult(
                symbol=symbol, proposed_quantity=0, approved_quantity=0,
                is_downsized=False, is_rejected=True, nav_limit_breached=False,
                adv_limit_breached=False, reason="Invalid proposed quantity."
            )

        # Missing/stale liquidity data must not be read as "no ADV constraint".
        if twenty_day_adv is None or not (twenty_day_adv > 0):
            logger.warning(
                "Order for %s REJECTED: ADV unavailable or non-positive (%r).",
                symbol, twenty_day_adv
            )
            return OrderCheckResult(
                symbol=symbol, proposed_quantity=proposed_quantity, approved_quantity=0,
                is_downsized=False, is_rejected=True, nav_limit_breached=False,
                adv_limit_breached=True,
                reason="Order rejected: ADV unavailable or non-positive; liquidity limit cannot be evaluated."
            )

        # 1. ADV Limit Check (applies to both BUY and SELL - market impact is side-agnostic)
        max_adv_shares = int(twenty_day_adv * self.max_adv_pct)

        # 2. NAV Limit Check on the ABSOLUTE resulting exposure, inclusive of
        #    exposure already committed by unfilled orders.
        nav_limit_value = portfolio_nav * self.max_nav_pct
        effective_current = current_position_value + pending_exposure_value
        signed_direction = 1 if side_upper == "BUY" else -1

        increases_exposure = (
            effective_current == 0.0
            or (signed_direction > 0) == (effective_current > 0.0)
        )
        if increases_exposure:
            # Room remaining on the current side. Already at/over the cap -> 0.
            headroom_value = nav_limit_value - abs(effective_current)
        else:
            # De-risking: unwind the whole position, then re-establish on the
            # opposite side up to the same absolute cap.
            headroom_value = abs(effective_current) + nav_limit_value
        max_nav_shares = max(0, math.floor(headroom_value / price))

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
            logger.info(
                "Order for %s downsized from %d to %d.",
                symbol, proposed_quantity, max_compliant_qty
            )
            return OrderCheckResult(
                symbol=symbol, proposed_quantity=proposed_quantity,
                approved_quantity=max_compliant_qty, is_downsized=True,
                is_rejected=False, nav_limit_breached=nav_breached,
                adv_limit_breached=adv_breached,
                reason=f"Downsized to {max_compliant_qty} shares to meet risk limits."
            )
        else:
            logger.warning("Order for %s REJECTED due to concentration risk limits.", symbol)
            return OrderCheckResult(
                symbol=symbol, proposed_quantity=proposed_quantity,
                approved_quantity=0, is_downsized=False,
                is_rejected=True, nav_limit_breached=nav_breached,
                adv_limit_breached=adv_breached,
                reason="Order rejected due to concentration risk limits."
            )

    @staticmethod
    def calculate_hhi(position_values: List[float]) -> Dict[str, float]:
        """
        Calculates the Herfindahl-Hirschman Index (HHI) and the Effective Number
        of Assets (N_eff) over GROSS exposure.

        Weights are absolute market values divided by total gross exposure, so a
        long and a short of equal size count as two positions rather than
        netting to zero. Feed net values instead if the risk policy nets them.

        HHI = sum(w_i^2), N_eff = 1 / HHI.

        Returns NaN for both when gross exposure is zero: concentration is
        undefined for an empty portfolio, and NaN keeps that from reading as
        "maximally diversified" or tripping an `N_eff < threshold` alert.
        """
        abs_values = [abs(v) for v in position_values if math.isfinite(v) and abs(v) > 0]
        total_val = sum(abs_values)

        if total_val == 0:
            return {"hhi": float("nan"), "effective_n": float("nan")}

        weights = [v / total_val for v in abs_values]
        hhi = sum(w ** 2 for w in weights)
        effective_n = 1.0 / hhi if hhi > 0 else float("nan")

        return {
            "hhi": round(hhi, 4),
            "effective_n": round(effective_n, 2)
        }
