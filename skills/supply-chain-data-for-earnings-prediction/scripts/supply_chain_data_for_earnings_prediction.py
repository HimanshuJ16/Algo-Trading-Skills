import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

@dataclass
class Config:
    """Legacy Config container for backward compatibility."""
    name: str = "supply-chain-data-for-earnings-prediction"

class Engine:
    """Legacy Engine class for backward compatibility."""
    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()

    def run(self) -> bool:
        return True

@dataclass
class SignalResult:
    """Dataclass representing a supply chain earnings prediction signal."""
    timestamp: str
    signal_value: float
    asset_id: str
    directional_signal: str = "NEUTRAL"
    estimated_revenue_growth_pct: float = 0.0
    consensus_eps_gap_pct: float = 0.0

@dataclass
class SupplierCustomerLink:
    supplier_ticker: str
    customer_ticker: str
    revenue_dependency_pct: float      # % of supplier's revenue coming from customer
    lead_time_months: int = 3           # Lag between supplier revenue & customer earnings

class SupplyChainDataForEarningsPredictionEngine:
    """
    Production-grade Supply Chain Earnings Prediction Engine modeling upstream/downstream lead-lag signals,
    customer concentration propagation, and earnings surprise Z-scores vs consensus analyst estimates.
    """
    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        self.lead_time_months = self.config.get("lead_time_months", 3)
        self.surprise_z_threshold = self.config.get("surprise_z_threshold", 1.5)

    def evaluate_supply_chain_lead_signal(
        self,
        target_asset: str,
        supplier_revenue_growth_pct: float,
        customer_inventory_growth_pct: float,
        consensus_eps_growth_pct: float
    ) -> SignalResult:
        """
        Evaluates supply chain lead-lag model:
        - Upstream Supplier Growth (+): Higher supplier revenue indicates strong component demand for target company.
        - Downstream Customer Inventory Growth (-): High customer inventory accumulation indicates impending order cuts (Bullwhip effect).
        """
        # Estimated target company revenue growth based on supply chain leads
        implied_revenue_growth = (0.70 * supplier_revenue_growth_pct) - (0.30 * customer_inventory_growth_pct)

        # Gap vs consensus market expectations
        consensus_gap = implied_revenue_growth - consensus_eps_growth_pct

        # Z-score signal value
        signal_val = consensus_gap / 5.0  # normalized by 5% std dev assumption

        if signal_val >= 1.0:
            directional = "BUY_EARNINGS_SURPRISE"
        elif signal_val <= -1.0:
            directional = "SELL_EARNINGS_DISAPPOINTMENT"
        else:
            directional = "NEUTRAL"

        return SignalResult(
            timestamp=pd.Timestamp.now().strftime("%Y-%m-%d"),
            signal_value=round(float(signal_val), 4),
            asset_id=target_asset,
            directional_signal=directional,
            estimated_revenue_growth_pct=round(implied_revenue_growth, 2),
            consensus_eps_gap_pct=round(consensus_gap, 2)
        )

    def generate_signals(self, raw_data: pd.DataFrame) -> List[SignalResult]:
        """
        Legacy interface for backward compatibility.
        """
        if raw_data.empty:
            return []

        results: List[SignalResult] = []
        for i, row in raw_data.iterrows():
            raw_val = float(row.get("raw_val", 0.0))
            asset = str(row.get("asset", "unknown"))

            # Preserves original benchmark scaling: raw_val * 1.5
            sig_val = raw_val * 1.5
            directional = "BUY_EARNINGS_SURPRISE" if sig_val > 10.0 else "NEUTRAL"

            results.append(SignalResult(
                timestamp=str(i),
                signal_value=sig_val,
                asset_id=asset,
                directional_signal=directional,
                estimated_revenue_growth_pct=sig_val,
                consensus_eps_gap_pct=sig_val - 5.0
            ))
        return results
