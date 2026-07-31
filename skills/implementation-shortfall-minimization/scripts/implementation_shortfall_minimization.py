import math
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class ExecutedTradeFill:
    fill_id: str
    quantity: int
    fill_price: float
    explicit_fee_usd: float
    timestamp_nanos: int

@dataclass
class ImplementationShortfallReport:
    symbol: str
    side: str
    total_order_qty: int
    executed_qty: int
    unfilled_qty: int
    decision_price_p0: float
    volume_weighted_executed_price: float
    final_market_price: float
    market_impact_cost_usd: float
    opportunity_cost_usd: float
    explicit_fees_usd: float
    total_implementation_shortfall_usd: float
    total_implementation_shortfall_bps: float
    status: str                         # 'IS_EVALUATION_SUCCESS'
    audit_notes: str

class ImplementationShortfallEngine:
    """
    Quantitative execution engine implementing Almgren-Chriss optimal trajectory planning and
    Perold (1988) Implementation Shortfall cost decomposition (Impact, Opportunity, Explicit Fees) in basis points.
    """
    def __init__(self):
        pass

    def calculate_almgren_chriss_trajectory(
        self,
        total_qty: int,
        n_intervals: int = 5,
        risk_aversion_lambda: float = 1e-4  # Higher lambda -> Front-loaded aggressive schedule
    ) -> List[int]:
        """
        Generates Almgren-Chriss optimal trading trajectory across N intervals.
        """
        if total_qty <= 0 or n_intervals <= 0:
            raise ValueError("Total quantity and intervals must be > 0.")

        # Almgren-Chriss exponential decay trajectory parameter kappa
        kappa = math.sqrt(risk_aversion_lambda) if risk_aversion_lambda > 0 else 0.0

        raw_shares = []
        for j in range(n_intervals):
            if kappa > 0:
                # Exponential decay schedule
                weight = math.sinh(kappa * (n_intervals - j)) / math.sinh(kappa * n_intervals)
                next_weight = math.sinh(kappa * (n_intervals - j - 1)) / math.sinh(kappa * n_intervals)
                slice_fraction = weight - next_weight
            else:
                # Linear TWAP schedule
                slice_fraction = 1.0 / float(n_intervals)
            raw_shares.append(int(round(total_qty * slice_fraction)))

        # Adjust rounding difference on final interval
        sum_shares = sum(raw_shares)
        if sum_shares != total_qty:
            raw_shares[-1] += (total_qty - sum_shares)

        return raw_shares

    def evaluate_implementation_shortfall(
        self,
        symbol: str,
        side: str,                           # 'BUY' or 'SELL'
        total_order_qty: int,
        decision_price_p0: float,
        executed_fills: List[ExecutedTradeFill],
        final_market_price: float
    ) -> ImplementationShortfallReport:
        """
        Decomposes Implementation Shortfall into Impact Cost, Opportunity Cost, and Explicit Fees (Perold 1988).
        """
        if total_order_qty <= 0 or decision_price_p0 <= 0:
            raise ValueError("Total order quantity and decision price must be > 0.")

        side_clean = side.upper()
        if side_clean not in {"BUY", "SELL"}:
            raise ValueError(f"Invalid side '{side}'. Must be 'BUY' or 'SELL'.")

        is_buy = (side_clean == "BUY")
        executed_qty = sum(f.quantity for f in executed_fills)
        unfilled_qty = max(0, total_order_qty - executed_qty)

        total_exec_notional = sum(f.quantity * f.fill_price for f in executed_fills)
        vwap_exec_price = round(total_exec_notional / float(executed_qty), 4) if executed_qty > 0 else decision_price_p0

        total_explicit_fees = sum(f.explicit_fee_usd for f in executed_fills)

        # 1. Market Impact / Slippage Cost
        if is_buy:
            impact_cost = round(sum(f.quantity * (f.fill_price - decision_price_p0) for f in executed_fills), 2)
        else:
            impact_cost = round(sum(f.quantity * (decision_price_p0 - f.fill_price) for f in executed_fills), 2)

        # 2. Opportunity Cost (Unfilled Quantity)
        if is_buy:
            opportunity_cost = round(unfilled_qty * (final_market_price - decision_price_p0), 2)
        else:
            opportunity_cost = round(unfilled_qty * (decision_price_p0 - final_market_price), 2)

        # 3. Total Implementation Shortfall (USD and bps)
        total_is_usd = round(impact_cost + opportunity_cost + total_explicit_fees, 2)
        total_order_benchmark_notional = total_order_qty * decision_price_p0
        total_is_bps = round((total_is_usd / float(total_order_benchmark_notional)) * 10000.0, 2)

        notes = (
            f"IMPLEMENTATION SHORTFALL REPORT [{symbol} - {side_clean}]: "
            f"Decision Price $P_0$ = ${decision_price_p0:.2f}, Exec VWAP = ${vwap_exec_price:.2f}. "
            f"Executed {executed_qty:,}/{total_order_qty:,} shares ({unfilled_qty:,} unfilled). "
            f"Impact Cost = ${impact_cost:,.2f}, Opportunity Cost = ${opportunity_cost:,.2f}, Fees = ${total_explicit_fees:,.2f}. "
            f"Total IS = ${total_is_usd:,.2f} ({total_is_bps:.2f} bps)."
        )
        logger.info(notes)

        return ImplementationShortfallReport(
            symbol=symbol,
            side=side_clean,
            total_order_qty=total_order_qty,
            executed_qty=executed_qty,
            unfilled_qty=unfilled_qty,
            decision_price_p0=decision_price_p0,
            volume_weighted_executed_price=vwap_exec_price,
            final_market_price=final_market_price,
            market_impact_cost_usd=impact_cost,
            opportunity_cost_usd=opportunity_cost,
            explicit_fees_usd=total_explicit_fees,
            total_implementation_shortfall_usd=total_is_usd,
            total_implementation_shortfall_bps=total_is_bps,
            status="IS_EVALUATION_SUCCESS",
            audit_notes=notes
        )
