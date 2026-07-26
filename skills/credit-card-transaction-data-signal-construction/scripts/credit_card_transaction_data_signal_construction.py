import numpy as np
import logging
from dataclasses import dataclass
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

@dataclass
class QuarterlyPanelData:
    ticker: str
    fiscal_quarter: str               # e.g. '2025-Q1'
    panel_spend_usd: float            # Raw panel spend sample
    panel_transaction_count: int
    consensus_revenue_usd: float      # Wall Street consensus revenue estimate

@dataclass
class TransactionSignalResult:
    ticker: str
    fiscal_quarter: str
    implied_revenue_usd: float
    yoy_growth_pct: float
    predicted_surprise_pct: float
    signal: str                        # 'BEAT_BUY', 'MISS_SELL', 'NEUTRAL'
    confidence_score: float

class CreditCardTransactionSignalEngine:
    """
    Alternative Data pipeline engine for scaling credit card panel spend,
    computing YoY revenue growth, and predicting earnings consensus surprises.
    """
    def __init__(self, panel_scaling_multiplier: float = 45.0, surprise_threshold_pct: float = 2.5):
        self.panel_scaling_multiplier = panel_scaling_multiplier
        self.surprise_threshold_pct = surprise_threshold_pct

    def estimate_implied_revenue(self, panel_spend_usd: float) -> float:
        """
        Scales sample panel spend to total implied company revenue.
        """
        if panel_spend_usd < 0:
            raise ValueError("Panel spend cannot be negative.")
        return float(panel_spend_usd * self.panel_scaling_multiplier)

    def calculate_yoy_growth(self, current_implied_revenue: float, prior_year_implied_revenue: float) -> float:
        """
        Calculates Year-over-Year (YoY) revenue growth percentage.
        """
        if prior_year_implied_revenue <= 0:
            return 0.0
        return round(float((current_implied_revenue - prior_year_implied_revenue) / prior_year_implied_revenue * 100.0), 2)

    def generate_signal(
        self,
        current_data: QuarterlyPanelData,
        prior_year_data: Optional[QuarterlyPanelData] = None
    ) -> TransactionSignalResult:
        """
        Processes panel data and generates earnings beat/miss trading signals.
        """
        implied_rev = self.estimate_implied_revenue(current_data.panel_spend_usd)
        
        yoy_growth = 0.0
        if prior_year_data:
            prior_implied_rev = self.estimate_implied_revenue(prior_year_data.panel_spend_usd)
            yoy_growth = self.calculate_yoy_growth(implied_rev, prior_implied_rev)

        # Consensus Surprise calculation: ((Implied - Consensus) / Consensus) * 100
        if current_data.consensus_revenue_usd <= 0:
            raise ValueError("Consensus revenue must be positive.")

        surprise_pct = ((implied_rev - current_data.consensus_revenue_usd) / current_data.consensus_revenue_usd) * 100.0
        surprise_pct_rounded = round(float(surprise_pct), 2)

        if surprise_pct_rounded >= self.surprise_threshold_pct:
            signal = "BEAT_BUY"
            confidence = min(1.0, round(0.50 + abs(surprise_pct_rounded) / 20.0, 2))
        elif surprise_pct_rounded <= -self.surprise_threshold_pct:
            signal = "MISS_SELL"
            confidence = min(1.0, round(0.50 + abs(surprise_pct_rounded) / 20.0, 2))
        else:
            signal = "NEUTRAL"
            confidence = 0.50

        logger.info(
            f"Credit Card Signal [{current_data.ticker} {current_data.fiscal_quarter}]: "
            f"Implied=${implied_rev/1e6:.1f}M vs Consensus=${current_data.consensus_revenue_usd/1e6:.1f}M "
            f"(Surprise={surprise_pct_rounded:+.2f}%) -> {signal}"
        )

        return TransactionSignalResult(
            ticker=current_data.ticker,
            fiscal_quarter=current_data.fiscal_quarter,
            implied_revenue_usd=round(implied_rev, 2),
            yoy_growth_pct=yoy_growth,
            predicted_surprise_pct=surprise_pct_rounded,
            signal=signal,
            confidence_score=confidence
        )
