import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class BrokerProfile:
    broker_id: str
    name: str
    max_nav_pct_limit: float          # e.g. 0.35 (35% NAV cap)
    cds_spread_bps: float             # Credit Default Swap spread in bps (e.g. 85.0)
    max_cds_bps_threshold: float      # Max allowed CDS before blocking (e.g. 250.0)
    current_cash: float
    current_margin: float
    current_positions_value: float

@dataclass
class RoutingDecision:
    selected_broker_id: str
    is_rerouted: bool
    original_broker_id: str
    projected_nav_pct: float
    reason: str

class CounterpartyConcentrationMonitor:
    """
    Monitors prime broker counterparty concentration exposures, credit default signals,
    enforces % NAV caps, and provides smart failover order routing.
    """
    def __init__(self, brokers: List[BrokerProfile] = None):
        self.brokers: Dict[str, BrokerProfile] = {b.broker_id: b for b in (brokers or [])}

    def register_broker(self, broker: BrokerProfile):
        self.brokers[broker.broker_id] = broker

    def calculate_total_broker_exposure(self, broker_id: str) -> float:
        if broker_id not in self.brokers:
            return 0.0
        b = self.brokers[broker_id]
        return b.current_cash + b.current_margin + b.current_positions_value

    def calculate_portfolio_nav(self) -> float:
        return sum(self.calculate_total_broker_exposure(bid) for bid in self.brokers)

    def compute_hhi(self) -> float:
        total_nav = self.calculate_portfolio_nav()
        if total_nav <= 0:
            return 0.0
        weights = [self.calculate_total_broker_exposure(bid) / total_nav for bid in self.brokers]
        return round(float(sum(w ** 2 for w in weights)), 4)

    def route_order(self, target_broker_id: str, proposed_order_value: float) -> RoutingDecision:
        """
        Evaluates proposed order routing to target_broker_id against concentration caps and CDS thresholds.
        Re-routes to secondary broker if limits are breached.
        """
        total_nav = self.calculate_portfolio_nav()
        effective_nav = max(total_nav, proposed_order_value)  # Prevent div-by-zero on initial portfolio

        if target_broker_id not in self.brokers:
            raise ValueError(f"Unknown target broker {target_broker_id}")

        primary = self.brokers[target_broker_id]
        primary_current_exp = self.calculate_total_broker_exposure(target_broker_id)
        primary_proj_weight = (primary_current_exp + proposed_order_value) / effective_nav

        # Check Primary Broker Compliance
        is_cds_distressed = primary.cds_spread_bps > primary.max_cds_bps_threshold
        is_nav_breached = primary_proj_weight > primary.max_nav_pct_limit

        if not is_cds_distressed and not is_nav_breached:
            return RoutingDecision(
                selected_broker_id=target_broker_id,
                is_rerouted=False,
                original_broker_id=target_broker_id,
                projected_nav_pct=round(primary_proj_weight * 100, 2),
                reason="Order approved for primary broker within exposure limits."
            )

        # Primary Broker Breached! Search Secondary Brokers for Failover Routing
        logger.warning(
            f"Primary Broker {target_broker_id} BREACHED (NAV Weight: {primary_proj_weight*100:.1f}%, CDS: {primary.cds_spread_bps}bps). Searching failover..."
        )

        candidate_brokers: List[Tuple[str, float]] = []
        for bid, b in self.brokers.items():
            if bid == target_broker_id:
                continue
            if b.cds_spread_bps > b.max_cds_bps_threshold:
                continue
                
            curr_exp = self.calculate_total_broker_exposure(bid)
            proj_weight = (curr_exp + proposed_order_value) / effective_nav
            if proj_weight <= b.max_nav_pct_limit:
                candidate_brokers.append((bid, proj_weight))

        if candidate_brokers:
            # Select broker with lowest projected weight
            candidate_brokers.sort(key=lambda x: x[1])
            selected_id, selected_weight = candidate_brokers[0]
            logger.info(f"Order RE-ROUTED from {target_broker_id} to secondary broker {selected_id}.")
            return RoutingDecision(
                selected_broker_id=selected_id,
                is_rerouted=True,
                original_broker_id=target_broker_id,
                projected_nav_pct=round(selected_weight * 100, 2),
                reason=f"Primary broker {target_broker_id} limit breached. Re-routed to secondary broker {selected_id}."
            )
        else:
            logger.critical("NO COMPLIANT FAILOVER BROKER AVAILABLE FOR ROUTING!")
            return RoutingDecision(
                selected_broker_id=target_broker_id,
                is_rerouted=False,
                original_broker_id=target_broker_id,
                projected_nav_pct=round(primary_proj_weight * 100, 2),
                reason="All prime brokers exceed concentration limits or CDS thresholds. Execution blocked."
            )
