import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import pandas as pd

logger = logging.getLogger(__name__)

@dataclass
class SignalResult:
    timestamp: str
    signal_value: float
    asset_id: str

@dataclass
class OptionsTrade:
    trade_id: str
    asset_id: str                        # e.g. 'AAPL'
    option_symbol: str                   # e.g. 'AAPL240119C00150000'
    option_type: str                     # 'CALL', 'PUT'
    volume: int
    open_interest: int
    adv: float                           # Average daily volume of contract/underlying
    execution_price: float
    bid: float
    ask: float
    timestamp: str

@dataclass
class OptionsFlowAnomalyReport:
    trade_id: str
    asset_id: str
    option_symbol: str
    vol_oi_ratio: float
    vol_adv_ratio: float
    total_premium_usd: float
    aggressor_side: str                  # 'BUY_AT_ASK', 'SELL_AT_BID', 'MID_MARKET'
    classification: str                  # 'UNUSUAL_BULLISH_SWEEP', 'UNUSUAL_BEARISH_SWEEP', 'ROUTINE_FLOW'
    is_unusual: bool
    audit_notes: str

class OptionsFlowUnusualActivityDetectionEngine:
    """
    Options flow unusual activity detection engine scanning real-time trade feeds for volume-to-open-interest spikes (V/OI > 1.5),
    institutional premium blocks, and aggressive sweep orders.
    """
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.min_v_oi_ratio = float(self.config.get("min_v_oi_ratio", 1.5))
        self.min_v_adv_ratio = float(self.config.get("min_v_adv_ratio", 2.0))
        self.min_premium_usd = float(self.config.get("min_premium_usd", 100000.0))

    def generate_signals(self, raw_data: pd.DataFrame) -> List[SignalResult]:
        """
        Legacy signal generation method for backward compatibility.
        """
        if raw_data.empty:
            return []
        results = []
        for i, row in raw_data.iterrows():
            results.append(SignalResult(
                timestamp=str(i),
                signal_value=float(row.get("raw_val", 0) * 1.5),
                asset_id=row.get("asset", "unknown")
            ))
        return results

    def detect_unusual_activity(
        self, trade: OptionsTrade
    ) -> OptionsFlowAnomalyReport:
        """
        Audits options trade for volume-to-OI spikes, premium thresholds, and aggressor direction.
        """
        if trade.open_interest <= 0:
            v_oi = float(trade.volume)
        else:
            v_oi = trade.volume / float(trade.open_interest)

        if trade.adv <= 0:
            v_adv = float(trade.volume)
        else:
            v_adv = trade.volume / float(trade.adv)

        # Premium USD = Volume * Price * 100 multiplier
        total_premium = trade.volume * trade.execution_price * 100.0

        # Aggressor direction
        if trade.execution_price >= trade.ask:
            aggressor = "BUY_AT_ASK"
        elif trade.execution_price <= trade.bid:
            aggressor = "SELL_AT_BID"
        else:
            aggressor = "MID_MARKET"

        # Classification logic
        is_spike = (v_oi >= self.min_v_oi_ratio) and (v_adv >= self.min_v_adv_ratio) and (total_premium >= self.min_premium_usd)
        opt_type = trade.option_type.upper()

        if is_spike and aggressor == "BUY_AT_ASK":
            if opt_type == "CALL":
                classification = "UNUSUAL_BULLISH_SWEEP"
            else:
                classification = "UNUSUAL_BEARISH_SWEEP"
            is_unusual = True
        elif is_spike and aggressor == "SELL_AT_BID":
            if opt_type == "CALL":
                classification = "UNUSUAL_BEARISH_BLOCK"
            else:
                classification = "UNUSUAL_BULLISH_BLOCK"
            is_unusual = True
        elif is_spike:
            classification = "UNUSUAL_FLOW_NEUTRAL"
            is_unusual = True
        else:
            classification = "ROUTINE_FLOW"
            is_unusual = False

        notes = (
            f"OPTIONS FLOW AUDIT [{trade.option_symbol}]: Vol/OI = {v_oi:.2f}x (min {self.min_v_oi_ratio}x), "
            f"Vol/ADV = {v_adv:.2f}x, Premium = ${total_premium:,.2f}, Aggressor = {aggressor} -> Classification: '{classification}'."
        )
        if is_unusual:
            logger.warning(notes)
        else:
            logger.info(notes)

        return OptionsFlowAnomalyReport(
            trade_id=trade.trade_id,
            asset_id=trade.asset_id,
            option_symbol=trade.option_symbol,
            vol_oi_ratio=round(v_oi, 2),
            vol_adv_ratio=round(v_adv, 2),
            total_premium_usd=round(total_premium, 2),
            aggressor_side=aggressor,
            classification=classification,
            is_unusual=is_unusual,
            audit_notes=notes
        )
