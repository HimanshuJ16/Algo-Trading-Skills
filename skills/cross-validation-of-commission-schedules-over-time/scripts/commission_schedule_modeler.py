"""
cross-validation-of-commission-schedules-over-time: Time-varying broker commission schedule modeler
and historical fee impact auditor.
"""
from dataclasses import dataclass
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class CommissionTier:
    effective_start: str  # ISO date YYYY-MM-DD
    effective_end: str    # ISO date YYYY-MM-DD (or "9999-12-31")
    fixed_ticket_fee: float
    per_share_fee: float
    min_trade_fee: float
    pct_value_fee: float


@dataclass
class TradeCommissionResult:
    trade_id: str
    timestamp: str
    symbol: str
    shares: float
    price: float
    trade_value: float
    effective_tier_start: str
    calculated_commission: float


@dataclass
class FeeScheduleImpactReport:
    total_trades: int
    total_volume: float
    total_commission_historical: float
    total_commission_modern_flat: float
    historical_fee_drag_pct: float
    modern_fee_drag_pct: float
    pnl_impact_delta_usd: float
    message: str


class HistoricalCommissionModeler:
    """
    Applies time-varying broker commission schedules to historical trades
    to prevent retroactive modern fee structure bias.
    """

    def __init__(self, schedule: Optional[List[CommissionTier]] = None):
        self.schedule = schedule or self._default_schedule()

    @staticmethod
    def _default_schedule() -> List[CommissionTier]:
        return [
            CommissionTier("2000-01-01", "2016-12-31", fixed_ticket_fee=9.95, per_share_fee=0.005, min_trade_fee=9.95, pct_value_fee=0.0),
            CommissionTier("2017-01-01", "2019-09-30", fixed_ticket_fee=4.95, per_share_fee=0.005, min_trade_fee=4.95, pct_value_fee=0.0),
            CommissionTier("2019-10-01", "9999-12-31", fixed_ticket_fee=0.00, per_share_fee=0.000, min_trade_fee=0.00, pct_value_fee=0.0),
        ]

    def calculate_trade_commission(
        self,
        trade_id: str,
        timestamp: str,  # YYYY-MM-DD
        symbol: str,
        shares: float,
        price: float,
    ) -> TradeCommissionResult:
        trade_val = shares * price
        matching_tier = None

        for tier in self.schedule:
            if tier.effective_start <= timestamp <= tier.effective_end:
                matching_tier = tier
                break

        if not matching_tier:
            matching_tier = self.schedule[-1]  # fallback to latest

        raw_comm = matching_tier.fixed_ticket_fee + (shares * matching_tier.per_share_fee) + (trade_val * matching_tier.pct_value_fee)
        final_comm = max(matching_tier.min_trade_fee, raw_comm)

        return TradeCommissionResult(
            trade_id=trade_id,
            timestamp=timestamp,
            symbol=symbol,
            shares=shares,
            price=price,
            trade_value=round(trade_val, 2),
            effective_tier_start=matching_tier.effective_start,
            calculated_commission=round(final_comm, 2),
        )

    def audit_impact(
        self,
        trades: List[Dict[str, Any]],
        starting_capital: float = 100000.0,
    ) -> FeeScheduleImpactReport:
        tot_hist_fee = 0.0
        tot_modern_fee = 0.0
        tot_val = 0.0

        for t in trades:
            res_hist = self.calculate_trade_commission(
                t["id"], t["date"], t["symbol"], t["shares"], t["price"]
            )
            tot_hist_fee += res_hist.calculated_commission
            # Modern flat fee assumption (e.g. $0)
            tot_modern_fee += 0.0
            tot_val += t["shares"] * t["price"]

        hist_drag = (tot_hist_fee / starting_capital) * 100.0
        modern_drag = (tot_modern_fee / starting_capital) * 100.0
        delta = tot_hist_fee - tot_modern_fee

        msg = (
            f"Commission Audit ({len(trades)} trades): Historical Fee Drag={hist_drag:.2f}% "
            f"(${tot_hist_fee:.2f}) vs Modern Flat Drag={modern_drag:.2f}%. P&L Delta=${delta:.2f}."
        )
        logger.info(msg)

        return FeeScheduleImpactReport(
            total_trades=len(trades),
            total_volume=round(tot_val, 2),
            total_commission_historical=round(tot_hist_fee, 2),
            total_commission_modern_flat=round(tot_modern_fee, 2),
            historical_fee_drag_pct=round(hist_drag, 2),
            modern_fee_drag_pct=round(modern_drag, 2),
            pnl_impact_delta_usd=round(delta, 2),
            message=msg,
        )
