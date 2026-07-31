import math
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class MarketStateSpec:
    symbol: str                         # e.g. 'AAPL'
    base_quote_qty: int                 # Target passive order quantity (e.g. 1000 shares)
    latency_gap_ms: float               # Cross-venue cancellation latency gap in ms (e.g. 2.5 ms)
    volatility_annualized: float        # Annualized volatility (e.g. 0.25 = 25%)
    spread_bps: float                   # Baseline bid-ask spread in bps (e.g. 2.0 bps)
    min_lot_size: int = 100             # Minimum order lot threshold

@dataclass
class DefensiveSizingReport:
    symbol: str
    base_quote_qty: int
    defensive_quote_qty: int            # Scaled down quote quantity
    sniping_probability: float          # Adverse selection sniping probability (0.0 to 1.0)
    spread_multiplier: float            # Widened spread multiplier (e.g. 1.5x)
    is_quote_canceled: bool             # True if quote is completely pulled
    status: str                         # 'QUOTE_DEFENSIVELY_SIZED', 'HIGH_SNIPING_RISK_CANCEL', 'MIN_LOT_CANCEL'
    audit_notes: str

class LatencyArbitrageDefensiveSizingEngine:
    """
    Microstructure risk mitigation engine evaluating cross-venue latency gaps,
    modeling adverse selection sniping probabilities, dynamically scaling down passive quote sizes, and widening bid-ask spreads.
    """
    def __init__(
        self,
        max_sniping_prob_threshold: float = 0.50,
        lambda_scaling: float = 0.50
    ):
        self.max_sniping_prob_threshold = max_sniping_prob_threshold
        self.lambda_scaling = lambda_scaling

    def compute_sniping_probability(self, latency_gap_ms: float, volatility: float) -> float:
        """
        Calculates adverse selection sniping probability:
        P_snipe = 1 - exp(-lambda * volatility * latency_gap_ms)
        """
        if latency_gap_ms <= 0.0 or volatility <= 0.0:
            return 0.0

        exponent = -1.0 * self.lambda_scaling * volatility * latency_gap_ms
        p_snipe = 1.0 - math.exp(exponent)
        return min(1.0, max(0.0, round(p_snipe, 4)))

    def calculate_defensive_sizing(self, spec: MarketStateSpec) -> DefensiveSizingReport:
        """
        Computes defensive quote sizing, spread multipliers, and quote cancellation decisions based on sniping risk.
        """
        p_snipe = self.compute_sniping_probability(spec.latency_gap_ms, spec.volatility_annualized)

        # 1. Compute Scaled Defensive Quantity
        raw_defensive_qty = int(spec.base_quote_qty * (1.0 - p_snipe))

        # 2. Spread Widening Multiplier
        spread_mult = round(1.0 + (p_snipe * 2.0), 2)

        # 3. High Sniping Risk or Min Lot Cancellation Logic
        is_canceled = False
        final_qty = raw_defensive_qty

        if p_snipe >= self.max_sniping_prob_threshold:
            is_canceled = True
            final_qty = 0
            status = "HIGH_SNIPING_RISK_CANCEL"
            notes = (
                f"DEFENSIVE CANCEL [{spec.symbol}]: High Sniping Probability ({p_snipe:.2%}) "
                f"exceeds threshold ({self.max_sniping_prob_threshold:.2%}) due to {spec.latency_gap_ms:.1f}ms latency gap. Quote Pulled!"
            )
            logger.warning(notes)
            return DefensiveSizingReport(
                symbol=spec.symbol, base_quote_qty=spec.base_quote_qty,
                defensive_quote_qty=0, sniping_probability=p_snipe,
                spread_multiplier=spread_mult, is_quote_canceled=True,
                status=status, audit_notes=notes
            )

        if raw_defensive_qty < spec.min_lot_size:
            is_canceled = True
            final_qty = 0
            status = "MIN_LOT_CANCEL"
            notes = (
                f"DEFENSIVE CANCEL [{spec.symbol}]: Defensive Qty ({raw_defensive_qty}) "
                f"below Min Lot Size ({spec.min_lot_size}). Quote Pulled!"
            )
            logger.warning(notes)
            return DefensiveSizingReport(
                symbol=spec.symbol, base_quote_qty=spec.base_quote_qty,
                defensive_quote_qty=0, sniping_probability=p_snipe,
                spread_multiplier=spread_mult, is_quote_canceled=True,
                status=status, audit_notes=notes
            )

        status = "QUOTE_DEFENSIVELY_SIZED"
        notes = (
            f"QUOTE DEFENSIVELY SIZED [{spec.symbol}]: Base Qty = {spec.base_quote_qty:,} -> "
            f"Defensive Qty = {final_qty:,} shares. Sniping Risk = {p_snipe:.2%}, "
            f"Spread Multiplier = {spread_mult:.2f}x (Latency Gap = {spec.latency_gap_ms:.1f}ms)."
        )
        logger.info(notes)

        return DefensiveSizingReport(
            symbol=spec.symbol,
            base_quote_qty=spec.base_quote_qty,
            defensive_quote_qty=final_qty,
            sniping_probability=p_snipe,
            spread_multiplier=spread_mult,
            is_quote_canceled=False,
            status=status,
            audit_notes=notes
        )
