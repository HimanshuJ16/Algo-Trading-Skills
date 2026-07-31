import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Speed of light propagation constants (km/s)
SPEED_OF_LIGHT_AIR_KM_S = 299_700.0   # Microwave / Millimeter-Wave propagation in air (n ~ 1.0003)
SPEED_OF_LIGHT_FIBER_KM_S = 203_945.0 # Fiber Optic propagation in silica glass (n ~ 1.47)

@dataclass
class NetworkLinkConfig:
    link_id: str
    link_type: str                      # 'MICROWAVE' or 'FIBER'
    corridor_name: str                  # e.g. 'CHICAGO_CME_TO_NJ_NASDAQ'
    distance_km: float                  # Physical path distance in km
    bandwidth_mbps: float = 1000.0      # Link bandwidth

@dataclass
class WeatherLinkTelemetry:
    link_id: str
    current_weather: str                # 'CLEAR', 'LIGHT_RAIN', 'HEAVY_RAIN', 'STORM'
    packet_loss_pct: float              # Current packet loss percentage
    signal_to_noise_ratio_db: float     # SNR in dB

@dataclass
class NetworkLinkReport:
    corridor_name: str
    microwave_rtt_ms: float
    fiber_rtt_ms: float
    latency_savings_ms: float
    latency_advantage_pct: float
    selected_routing_link_id: str
    selected_link_type: str
    is_failover_active: bool
    status: str                         # 'ROUTE_MICROWAVE_PRIMARY', 'FAILOVER_TO_FIBER_RAIN_FADE'
    audit_notes: str

class NetworkLinkArbitratorEngine:
    """
    Cross-market network latency and link arbitration engine,
    evaluating microwave vs fiber light propagation physics, speed advantages, and automated rain fade failover routing.
    """
    def __init__(self, max_packet_loss_threshold_pct: float = 1.0):
        self.max_packet_loss_threshold_pct = max_packet_loss_threshold_pct

    def arbitrate_cross_market_links(
        self,
        microwave_cfg: NetworkLinkConfig,
        fiber_cfg: NetworkLinkConfig,
        microwave_telemetry: WeatherLinkTelemetry
    ) -> NetworkLinkReport:
        """
        Calculates propagation latency physics for Microwave vs Fiber links and audits weather failover telemetry.
        """
        if microwave_cfg.distance_km <= 0.0 or fiber_cfg.distance_km <= 0.0:
            raise ValueError("Link physical distances must be strictly positive.")

        # 1. Physics Propagation Latency Calculation
        # One-Way Latency (ms) = (Distance_km / Speed_km_s) * 1000.0
        micro_one_way_ms = (microwave_cfg.distance_km / SPEED_OF_LIGHT_AIR_KM_S) * 1000.0
        micro_rtt_ms = round(micro_one_way_ms * 2.0, 3)

        fiber_one_way_ms = (fiber_cfg.distance_km / SPEED_OF_LIGHT_FIBER_KM_S) * 1000.0
        fiber_rtt_ms = round(fiber_one_way_ms * 2.0, 3)

        savings_ms = round(fiber_rtt_ms - micro_rtt_ms, 3)
        advantage_pct = round((savings_ms / fiber_rtt_ms) * 100.0, 2) if fiber_rtt_ms > 0 else 0.0

        # 2. Weather & Telemetry Rain Fade Audit
        is_rain_fade = (
            microwave_telemetry.current_weather in ["HEAVY_RAIN", "STORM"] or
            microwave_telemetry.packet_loss_pct > self.max_packet_loss_threshold_pct
        )

        if is_rain_fade:
            status = "FAILOVER_TO_FIBER_RAIN_FADE"
            selected_link_id = fiber_cfg.link_id
            selected_type = "FIBER"
            notes = (
                f"LINK FAILOVER [{microwave_cfg.corridor_name}]: Microwave degraded by "
                f"weather ('{microwave_telemetry.current_weather}', Packet Loss = {microwave_telemetry.packet_loss_pct:.1f}% > {self.max_packet_loss_threshold_pct}%). "
                f"Routed to Backup Fiber '{fiber_cfg.link_id}' (RTT = {fiber_rtt_ms:.3f}ms)."
            )
            logger.warning(notes)
        else:
            status = "ROUTE_MICROWAVE_PRIMARY"
            selected_link_id = microwave_cfg.link_id
            selected_type = "MICROWAVE"
            notes = (
                f"PRIMARY ROUTE [{microwave_cfg.corridor_name}]: Microwave active ({microwave_cfg.link_id}). "
                f"RTT = {micro_rtt_ms:.3f}ms vs Fiber RTT = {fiber_rtt_ms:.3f}ms "
                f"(Latency Advantage = {savings_ms:.3f}ms / {advantage_pct:.1f}% faster). Weather = '{microwave_telemetry.current_weather}'."
            )
            logger.info(notes)

        return NetworkLinkReport(
            corridor_name=microwave_cfg.corridor_name,
            microwave_rtt_ms=micro_rtt_ms,
            fiber_rtt_ms=fiber_rtt_ms,
            latency_savings_ms=savings_ms,
            latency_advantage_pct=advantage_pct,
            selected_routing_link_id=selected_link_id,
            selected_link_type=selected_type,
            is_failover_active=is_rain_fade,
            status=status,
            audit_notes=notes
        )
