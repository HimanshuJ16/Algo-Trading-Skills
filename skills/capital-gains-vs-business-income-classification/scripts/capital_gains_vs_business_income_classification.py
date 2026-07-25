import logging
from dataclasses import dataclass
from enum import Enum
import datetime
from typing import List, Dict

logger = logging.getLogger(__name__)

class AssetClass(Enum):
    EQUITY = 1
    DERIVATIVE = 2  # Futures & Options

class TaxCategory(Enum):
    SPECULATIVE_BUSINESS = 1
    NON_SPECULATIVE_BUSINESS = 2
    SHORT_TERM_CAPITAL_GAINS = 3
    LONG_TERM_CAPITAL_GAINS = 4

@dataclass
class ClosedTrade:
    trade_id: str
    symbol: str
    asset_class: AssetClass
    open_time: datetime.datetime
    close_time: datetime.datetime
    net_pnl: float

class TaxClassificationEngine:
    """
    Classifies closed trades into tax categories based on asset class and holding period.
    Designed for automated end-of-year back-office reporting.
    """
    def __init__(self, is_professional_trader: bool = True, ltcg_threshold_days: int = 365):
        self.is_professional_trader = is_professional_trader
        self.ltcg_threshold_days = ltcg_threshold_days

    def classify_trade(self, trade: ClosedTrade) -> TaxCategory:
        if trade.close_time < trade.open_time:
            raise ValueError(f"Trade {trade.trade_id} has negative holding period.")
            
        hold_time = trade.close_time - trade.open_time
        hold_days = hold_time.days
        is_intraday = hold_days == 0 and trade.open_time.date() == trade.close_time.date()

        if trade.asset_class == AssetClass.DERIVATIVE:
            # F&O are universally treated as non-speculative business income in many jurisdictions
            return TaxCategory.NON_SPECULATIVE_BUSINESS
            
        if trade.asset_class == AssetClass.EQUITY:
            if is_intraday:
                # Intraday equity is speculative business (no delivery)
                return TaxCategory.SPECULATIVE_BUSINESS
                
            if hold_days >= self.ltcg_threshold_days:
                # Long holds are almost always capital gains
                return TaxCategory.LONG_TERM_CAPITAL_GAINS
                
            # For holds between 1 day and LTCG threshold
            if self.is_professional_trader:
                return TaxCategory.NON_SPECULATIVE_BUSINESS
            else:
                return TaxCategory.SHORT_TERM_CAPITAL_GAINS

        raise ValueError(f"Unknown Asset Class: {trade.asset_class}")

    def aggregate_pnl(self, trades: List[ClosedTrade]) -> Dict[TaxCategory, float]:
        summary = {
            TaxCategory.SPECULATIVE_BUSINESS: 0.0,
            TaxCategory.NON_SPECULATIVE_BUSINESS: 0.0,
            TaxCategory.SHORT_TERM_CAPITAL_GAINS: 0.0,
            TaxCategory.LONG_TERM_CAPITAL_GAINS: 0.0
        }
        
        for trade in trades:
            category = self.classify_trade(trade)
            summary[category] += trade.net_pnl
            
        return summary
