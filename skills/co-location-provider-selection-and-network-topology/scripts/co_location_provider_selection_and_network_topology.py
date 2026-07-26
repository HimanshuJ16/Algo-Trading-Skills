import logging
from dataclasses import dataclass
from typing import List, Dict

logger = logging.getLogger(__name__)

# Standard propagation constants in microseconds per kilometer
PROPAGATION_SPEED_FIBER_US_KM = 5.0    # ~200,000 km/s in glass (5 us per km)
PROPAGATION_SPEED_MICROWAVE_US_KM = 3.333 # ~300,000 km/s in air (3.333 us per km)

@dataclass
class FacilitySpec:
    name: str
    location_code: str          # e.g. 'NY4', 'AURORA', 'LD4'
    rack_cost_mrc: float        # Monthly recurring cost per rack ($)
    power_kw: float             # Power capacity in kW
    power_cost_per_kw: float    # Cost per kW ($)
    cross_connect_mrc: float    # Cross-connect MRC ($)

@dataclass
class NetworkLinkSpec:
    source: str
    destination: str
    distance_km: float
    medium_type: str            # 'FIBER' or 'MICROWAVE'
    num_switches: int = 2       # Number of network switch hops
    switch_latency_us: float = 0.200 # Latency per switch in microseconds (200ns)

@dataclass
class LatencyBudgetResult:
    propagation_delay_us: float
    switch_delay_us: float
    total_one_way_us: float
    total_rtt_us: float

class ColocationTopologyEvaluator:
    """
    Quantitative framework for modeling cross-venue network topologies,
    decomposing latency budgets, and computing total cost of ownership (TCO).
    """
    @staticmethod
    def calculate_latency_budget(link: NetworkLinkSpec) -> LatencyBudgetResult:
        """
        Calculates propagation and network hop delays for a given topology link.
        """
        if link.distance_km < 0:
            raise ValueError("Distance cannot be negative.")

        if link.medium_type.upper() == 'FIBER':
            speed_factor = PROPAGATION_SPEED_FIBER_US_KM
        elif link.medium_type.upper() == 'MICROWAVE':
            speed_factor = PROPAGATION_SPEED_MICROWAVE_US_KM
        else:
            raise ValueError(f"Unsupported medium type: {link.medium_type}")

        propagation_delay_us = link.distance_km * speed_factor
        switch_delay_us = link.num_switches * link.switch_latency_us
        total_one_way_us = propagation_delay_us + switch_delay_us
        total_rtt_us = total_one_way_us * 2.0

        return LatencyBudgetResult(
            propagation_delay_us=round(propagation_delay_us, 3),
            switch_delay_us=round(switch_delay_us, 3),
            total_one_way_us=round(total_one_way_us, 3),
            total_rtt_us=round(total_rtt_us, 3)
        )

    @staticmethod
    def calculate_facility_tco(facility: FacilitySpec) -> float:
        """
        Calculates monthly total cost of ownership (TCO) for a facility setup.
        """
        total_power_cost = facility.power_kw * facility.power_cost_per_kw
        total_tco = facility.rack_cost_mrc + total_power_cost + facility.cross_connect_mrc
        return round(total_tco, 2)

    def evaluate_colocation_setup(
        self,
        facilities: List[FacilitySpec],
        links: List[NetworkLinkSpec],
        latency_weight: float = 0.7,
        cost_weight: float = 0.3
    ) -> List[Dict[str, any]]:
        """
        Scores candidate colocation facilities based on weighted latency and cost.
        """
        results = []
        for fac in facilities:
            tco = self.calculate_facility_tco(fac)
            
            # Find relevant links originating from this facility
            fac_links = [l for l in links if l.source == fac.location_code or l.destination == fac.location_code]
            avg_rtt = 0.0
            if fac_links:
                rtts = [self.calculate_latency_budget(l).total_rtt_us for l in fac_links]
                avg_rtt = sum(rtts) / len(rtts)
                
            results.append({
                "facility": fac.name,
                "code": fac.location_code,
                "tco_mrc": tco,
                "avg_rtt_us": round(avg_rtt, 3)
            })
            
        return results
