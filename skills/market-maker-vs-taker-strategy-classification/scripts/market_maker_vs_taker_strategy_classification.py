import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class ExecutedTradeLog:
    trade_id: str
    symbol: str
    is_maker: bool                       # True if passive limit order fill, False if taker
    executed_price: float
    quantity: float
    fee_paid_usd: float                 # Positive if fee paid, negative if rebate received

@dataclass
class StrategyClassificationReport:
    strategy_id: str
    total_trades_count: int
    maker_trades_count: int
    taker_trades_count: int
    total_volume_shares: float
    maker_volume_shares: float
    taker_volume_shares: float
    maker_volume_ratio: float           # R_maker = Maker_Vol / Total_Vol
    total_gross_notional_usd: float
    net_fees_paid_usd: float
    effective_fee_rate_bps: float       # Net_Fees / Gross_Notional * 10000
    classification: str                 # 'PURE_MAKER_STRATEGY', 'PURE_TAKER_STRATEGY', 'HYBRID_MAKER_TAKER_STRATEGY'
    status: str                         # 'STRATEGY_CLASSIFICATION_SUCCESS'
    audit_notes: str

class MarketMakerVsTakerClassifierEngine:
    """
    Quantitative strategy classification engine categorizing trading algorithms as Pure Maker, Pure Taker, or Hybrid
    based on execution volume ratios, fee schedule attribution, and rebate capture.
    """
    def __init__(self, pure_maker_threshold_ratio: float = 0.80, pure_taker_threshold_ratio: float = 0.20):
        self.pure_maker_threshold_ratio = pure_maker_threshold_ratio
        self.pure_taker_threshold_ratio = pure_taker_threshold_ratio

    def classify_strategy_executions(
        self,
        strategy_id: str,
        trades: List[ExecutedTradeLog]
    ) -> StrategyClassificationReport:
        """
        Audits trade execution log, computes Maker Volume Ratio, effective fee bps, and classifies strategy.
        """
        if not trades:
            raise ValueError("Executed trade log cannot be empty.")

        tot_trades = len(trades)
        maker_count = 0
        taker_count = 0

        tot_vol = 0.0
        maker_vol = 0.0
        taker_vol = 0.0

        tot_notional = 0.0
        net_fees = 0.0

        for t in trades:
            qty = abs(t.quantity)
            notional = t.executed_price * qty
            tot_vol += qty
            tot_notional += notional
            net_fees += t.fee_paid_usd

            if t.is_maker:
                maker_count += 1
                maker_vol += qty
            else:
                taker_count += 1
                taker_vol += qty

        maker_ratio = round(maker_vol / tot_vol, 4) if tot_vol > 0 else 0.0
        effective_fee_bps = round((net_fees / tot_notional) * 10000.0, 2) if tot_notional > 0 else 0.0

        # Classification Logic
        if maker_ratio >= self.pure_maker_threshold_ratio:
            classification = "PURE_MAKER_STRATEGY"
        elif maker_ratio <= self.pure_taker_threshold_ratio:
            classification = "PURE_TAKER_STRATEGY"
        else:
            classification = "HYBRID_MAKER_TAKER_STRATEGY"

        notes = (
            f"STRATEGY CLASSIFICATION [{strategy_id}]: Classified as '{classification}'. "
            f"Maker Ratio = {maker_ratio:.1%} ({maker_count}/{tot_trades} trades). "
            f"Gross Notional = ${tot_notional:,.2f}, Net Fees = ${net_fees:,.2f} "
            f"(Effective Fee Rate = {effective_fee_bps:+.2f} bps)."
        )
        logger.info(notes)

        return StrategyClassificationReport(
            strategy_id=strategy_id,
            total_trades_count=tot_trades,
            maker_trades_count=maker_count,
            taker_trades_count=taker_count,
            total_volume_shares=round(tot_vol, 2),
            maker_volume_shares=round(maker_vol, 2),
            taker_volume_shares=round(taker_vol, 2),
            maker_volume_ratio=maker_ratio,
            total_gross_notional_usd=round(tot_notional, 2),
            net_fees_paid_usd=round(net_fees, 2),
            effective_fee_rate_bps=effective_fee_bps,
            classification=classification,
            status="STRATEGY_CLASSIFICATION_SUCCESS",
            audit_notes=notes
        )
