import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class TuningConfig:
    symbol: str
    exchange_max_mps: float = 500.0     # Max messages per second ceiling
    target_safety_buffer_pct: float = 80.0 # Target safety buffer (80% = 400 MPS target)
    min_reprice_delay_ms: float = 1.0   # Hardware minimum reprice delay
    max_reprice_delay_ms: float = 500.0 # Upper bound delay cap
    price_threshold_bps: float = 2.0    # Minimum price movement required to trigger quote update

@dataclass
class MarketState:
    ticks_per_sec: float                # Microstructure tick arrival frequency
    price_volatility_bps: float         # 1-minute price volatility in basis points
    active_quoting_pairs: int = 2       # Number of active bid/ask order pairs (e.g. 2 = 1 bid + 1 ask)

@dataclass
class TuningReport:
    symbol: str
    unthrottled_message_rate_mps: float
    target_safety_limit_mps: float
    recommended_reprice_delay_ms: float
    projected_message_rate_mps: float
    adverse_selection_exposure_score: float
    is_tuning_required: bool
    status: str                         # 'RATE_LIMIT_TUNING_APPLIED', 'DIRECT_PASS_NO_TUNING_REQUIRED'
    audit_notes: str

class MessageRateLatencyTunerEngine:
    """
    Quantitative rate-limit vs adverse selection latency tuning engine,
    dynamically balancing quote repricing delays against exchange messages-per-second (MPS) ceilings.
    """
    def __init__(self):
        pass

    def tune_quote_reprice_parameters(
        self,
        config: TuningConfig,
        state: MarketState
    ) -> TuningReport:
        """
        Calculates unthrottled message velocity, determines optimal quote reprice delay, and audits adverse selection.
        """
        symbol = config.symbol
        if config.exchange_max_mps <= 0.0:
            raise ValueError("Exchange max MPS limit must be strictly positive.")
        if state.active_quoting_pairs <= 0:
            raise ValueError("Active quoting pairs must be positive.")

        # 1. Target Safety Limit (MPS)
        target_mps = round(config.exchange_max_mps * (config.target_safety_buffer_pct / 100.0), 2)

        # 2. Unthrottled Message Velocity (MPS)
        # Every tick triggers reprice messages for all active quoting pairs
        unthrottled_mps = round(state.ticks_per_sec * state.active_quoting_pairs, 2)

        # 3. Optimal Reprice Delay Calculation (ms)
        # Required delay to restrict message rate to target_mps:
        # Delay (sec) = ActiveQuotingPairs / target_mps
        # Delay (ms) = (ActiveQuotingPairs / target_mps) * 1000.0
        required_delay_ms = (state.active_quoting_pairs / target_mps) * 1000.0
        optimal_delay_ms = round(
            min(max(config.min_reprice_delay_ms, required_delay_ms), config.max_reprice_delay_ms), 2
        )

        # 4. Projected MPS with Tuned Reprice Delay
        # Projected MPS = (ActiveQuotingPairs / optimal_delay_ms) * 1000.0
        projected_mps = round((state.active_quoting_pairs / optimal_delay_ms) * 1000.0, 2)

        # 5. Adverse Selection Exposure Score (Delay_ms * Volatility_bps)
        exposure_score = round(optimal_delay_ms * state.price_volatility_bps, 2)

        is_tuning_req = (unthrottled_mps > target_mps)

        if is_tuning_req:
            status = "RATE_LIMIT_TUNING_APPLIED"
            notes = (
                f"RATE LIMIT TUNING APPLIED [{symbol}]: Unthrottled rate {unthrottled_mps:.0f} MPS "
                f"exceeds target safety limit ({target_mps:.0f} MPS). Tuned reprice delay from "
                f"{config.min_reprice_delay_ms:.1f}ms to {optimal_delay_ms:.1f}ms "
                f"(Projected Rate = {projected_mps:.0f} MPS, Adverse Exposure Score = {exposure_score:.1f})."
            )
            logger.info(notes)
        else:
            status = "DIRECT_PASS_NO_TUNING_REQUIRED"
            optimal_delay_ms = config.min_reprice_delay_ms
            projected_mps = unthrottled_mps
            exposure_score = round(optimal_delay_ms * state.price_volatility_bps, 2)
            notes = (
                f"DIRECT PASS [{symbol}]: Unthrottled rate {unthrottled_mps:.0f} MPS is within "
                f"target safety limit ({target_mps:.0f} MPS). Retained min delay = {config.min_reprice_delay_ms:.1f}ms."
            )
            logger.info(notes)

        return TuningReport(
            symbol=symbol,
            unthrottled_message_rate_mps=unthrottled_mps,
            target_safety_limit_mps=target_mps,
            recommended_reprice_delay_ms=optimal_delay_ms,
            projected_message_rate_mps=projected_mps,
            adverse_selection_exposure_score=exposure_score,
            is_tuning_required=is_tuning_req,
            status=status,
            audit_notes=notes
        )
