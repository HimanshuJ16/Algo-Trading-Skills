import math
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class OptionLeg:
    option_type: str                     # 'CALL' or 'PUT'
    action: str                          # 'BUY' or 'SELL'
    strike: float
    quantity: int = 1
    premium: float = 0.0                 # Per share option premium

@dataclass
class MultiLegStrategyPayload:
    symbol: str
    underlying_price: float
    legs: List[OptionLeg]

@dataclass
class MarginOptimizationReport:
    symbol: str
    strategy_type: str                  # 'VERTICAL_SPREAD', 'IRON_CONDOR', 'CUSTOM'
    uncombined_naked_margin_usd: float
    optimized_combo_margin_usd: float
    margin_savings_usd: float
    margin_reduction_pct: float
    status: str                         # 'MARGIN_OPTIMIZED_SUCCESS'
    audit_notes: str

class MultiLegStrategyMarginOptimizerEngine:
    """
    Multi-leg options strategy margin optimization engine evaluating Reg T and Portfolio Margin requirements,
    combining vertical spreads and Iron Condors to minimize capital tie-up.
    """
    def __init__(self):
        pass

    def _calculate_naked_leg_margin(self, leg: OptionLeg, underlying_price: float) -> float:
        """Calculates independent Reg T margin for a single option leg."""
        q = abs(leg.quantity)
        opt_upper = leg.option_type.upper()
        act_upper = leg.action.upper()
        p = leg.premium
        s = underlying_price
        k = leg.strike

        if act_upper == "BUY":
            # Long option margin = Premium paid * 100 * Q
            return p * 100.0 * q

        # Short Naked Options
        if opt_upper == "CALL":
            otm = max(0.0, k - s)
            m1 = (0.20 * s - otm + p) * 100.0 * q
            m2 = (0.10 * s + p) * 100.0 * q
            return max(m1, m2)
        elif opt_upper == "PUT":
            otm = max(0.0, s - k)
            m1 = (0.20 * s - otm + p) * 100.0 * q
            m2 = (0.10 * k + p) * 100.0 * q
            return max(m1, m2)
        return 0.0

    def optimize_strategy_margin(self, payload: MultiLegStrategyPayload) -> MarginOptimizationReport:
        """
        Detects multi-leg strategy structures, computes uncombined naked margin vs recognized combo max-loss margin.
        """
        if not payload.legs or payload.underlying_price <= 0:
            raise ValueError("Legs list cannot be empty and underlying price must be positive.")

        s = payload.underlying_price
        legs = payload.legs

        # 1. Uncombined Naked Margin Calculation
        uncombined_margin = sum(self._calculate_naked_leg_margin(leg, s) for leg in legs)

        # 2. Strategy Pattern Recognition & Combo Margin Calculation
        num_legs = len(legs)
        strategy_type = "CUSTOM"
        combo_margin = uncombined_margin

        # Net Credit / Debit calculation
        net_credit_per_share = sum(
            (leg.premium if leg.action.upper() == "SELL" else -leg.premium) for leg in legs
        )

        if num_legs == 2:
            l1, l2 = legs[0], legs[1]
            if l1.option_type.upper() == l2.option_type.upper() and l1.action.upper() != l2.action.upper():
                strategy_type = "VERTICAL_SPREAD"
                strike_diff = abs(l1.strike - l2.strike)
                q = abs(l1.quantity)
                max_loss = (strike_diff - max(0.0, net_credit_per_share)) * 100.0 * q
                combo_margin = max(0.0, max_loss)

        elif num_legs == 4:
            calls = [l for l in legs if l.option_type.upper() == "CALL"]
            puts = [l for l in legs if l.option_type.upper() == "PUT"]

            if len(calls) == 2 and len(puts) == 2:
                # Check for Iron Condor (Bull Put Spread + Bear Call Spread)
                strategy_type = "IRON_CONDOR"
                call_strikes = sorted([l.strike for l in calls])
                put_strikes = sorted([l.strike for l in puts])

                call_width = abs(call_strikes[1] - call_strikes[0])
                put_width = abs(put_strikes[1] - put_strikes[0])
                max_width = max(call_width, put_width)
                q = abs(legs[0].quantity)

                max_loss = (max_width - max(0.0, net_credit_per_share)) * 100.0 * q
                combo_margin = max(0.0, max_loss)

        # 3. Savings Audit
        margin_savings = max(0.0, uncombined_margin - combo_margin)
        margin_reduction_pct = (margin_savings / uncombined_margin * 100.0) if uncombined_margin > 0 else 0.0

        notes = (
            f"MARGIN OPTIMIZED [{payload.symbol} - {strategy_type}]: Uncombined Naked Margin = ${uncombined_margin:,.2f}, "
            f"Optimized Combo Margin = ${combo_margin:,.2f}, Capital Savings = ${margin_savings:,.2f} ({margin_reduction_pct:.1f}% Reduction)."
        )
        logger.info(notes)

        return MarginOptimizationReport(
            symbol=payload.symbol,
            strategy_type=strategy_type,
            uncombined_naked_margin_usd=round(uncombined_margin, 2),
            optimized_combo_margin_usd=round(combo_margin, 2),
            margin_savings_usd=round(margin_savings, 2),
            margin_reduction_pct=round(margin_reduction_pct, 1),
            status="MARGIN_OPTIMIZED_SUCCESS",
            audit_notes=notes
        )
