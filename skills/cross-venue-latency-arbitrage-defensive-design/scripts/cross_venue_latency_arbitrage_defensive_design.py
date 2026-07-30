import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class PrimaryBookState:
    bid_price: float
    ask_price: float
    bid_volume: float
    ask_volume: float
    tick_size: float = 0.01

@dataclass
class LatencyProfile:
    cancel_rtt_us: float                # Market maker's cancel RTT (microseconds)
    hft_sweep_latency_us: float         # Competitor HFT sweep latency (microseconds)
    lead_event_timestamp_us: float

@dataclass
class LatencyArbitrageDefenseReport:
    micro_price: float
    mid_price: float
    toxicity_index_ticks: float
    latency_margin_us: float            # Positive => MM wins race, Negative => Risk of being picked off
    is_preemptive_cancel_triggered: bool
    defensive_bid_spread_ticks: float
    defensive_ask_spread_ticks: float
    defensive_quote_size: int
    recommendations: List[str]

class CrossVenueLatencyArbitrageDefense:
    """
    HFT market-making engine for detecting cross-venue latency arbitrage, calculating micro-price toxicity,
    and executing defensive quote pulls, spread widening, and size reductions.
    """
    def __init__(
        self,
        toxicity_threshold_ticks: float = 1.0,
        base_spread_ticks: float = 1.0,
        base_quote_size: int = 100
    ):
        self.toxicity_threshold_ticks = toxicity_threshold_ticks
        self.base_spread_ticks = base_spread_ticks
        self.base_quote_size = base_quote_size

    def calculate_micro_price(self, book: PrimaryBookState) -> Tuple[float, float]:
        """
        Calculates volume-weighted Micro-Price and Mid-Price:
        P_micro = (V_ask * P_bid + V_bid * P_ask) / (V_bid + V_ask)
        """
        total_vol = book.bid_volume + book.ask_volume
        if total_vol <= 0:
            mid = (book.bid_price + book.ask_price) / 2.0
            return mid, mid

        mid = (book.bid_price + book.ask_price) / 2.0
        micro = (book.ask_volume * book.bid_price + book.bid_volume * book.ask_price) / total_vol
        return round(micro, 4), round(mid, 4)

    def evaluate_defense(
        self,
        book: PrimaryBookState,
        latency: LatencyProfile,
        cancel_sent_timestamp_us: float
    ) -> LatencyArbitrageDefenseReport:
        """
        Audits micro-price toxicity and cross-venue latency margin to issue defensive quoting directives.
        """
        micro_price, mid_price = self.calculate_micro_price(book)
        
        # Toxicity Index in Ticks: |P_micro - P_mid| / TickSize
        toxicity_ticks = round(abs(micro_price - mid_price) / book.tick_size, 2)

        # Latency Margin: (t_lead + t_hft_sweep) - (t_cancel_sent + t_cancel_rtt)
        hft_arrival = latency.lead_event_timestamp_us + latency.hft_sweep_latency_us
        mm_cancel_arrival = cancel_sent_timestamp_us + latency.cancel_rtt_us
        latency_margin_us = round(hft_arrival - mm_cancel_arrival, 2)

        is_cancel_triggered = False
        recommendations = []

        # Trigger Preemptive Cancel if Latency Margin is negative or Toxicity is high
        if latency_margin_us < 0 or toxicity_ticks >= self.toxicity_threshold_ticks:
            is_cancel_triggered = True
            msg = (
                f"LATENCY ARBITRAGE RISK DETECTED: Toxicity={toxicity_ticks} ticks, "
                f"Latency Margin={latency_margin_us}us. Pulling quotes on secondary venue!"
            )
            recommendations.append(msg)
            logger.critical(msg)

        # Dynamic Spread Widening & Size Reduction
        # Spread = Base * (1 + 0.5 * Toxicity)
        defensive_spread = round(self.base_spread_ticks * (1.0 + 0.5 * toxicity_ticks), 2)
        # Size = Base / (1 + Toxicity)
        defensive_size = max(1, int(self.base_quote_size / (1.0 + toxicity_ticks)))

        if not recommendations:
            recommendations.append("Order book balanced and latency margin safe. Normal market making.")

        return LatencyArbitrageDefenseReport(
            micro_price=micro_price,
            mid_price=mid_price,
            toxicity_index_ticks=toxicity_ticks,
            latency_margin_us=latency_margin_us,
            is_preemptive_cancel_triggered=is_cancel_triggered,
            defensive_bid_spread_ticks=defensive_spread,
            defensive_ask_spread_ticks=defensive_spread,
            defensive_quote_size=defensive_size,
            recommendations=recommendations
        )
