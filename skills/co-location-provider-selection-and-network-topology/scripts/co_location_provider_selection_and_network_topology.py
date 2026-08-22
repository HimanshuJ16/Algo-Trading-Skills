"""Co-location provider selection and cross-venue network topology modelling.

Decomposes one-way and round-trip latency budgets for candidate co-location
facilities, computes monthly total cost of ownership (TCO), and ranks
facilities with a multi-attribute utility (MAUT) penalty score.

Propagation constants are derived from first principles rather than hard-coded
round numbers:

*   ``c = 299_792.458 km/s`` (exact, SI definition of the metre) gives a vacuum
    floor of ``VACUUM_DELAY_US_KM = 3.3356 us/km``. No configured link may be
    modelled as faster than this.
*   Microwave/free-space RF travels at ``c / n_air`` with ``n_air ~= 1.0003``,
    i.e. ``3.3366 us/km``. The commonly quoted "3.33 us/km (300 km/ms)"
    shorthand implies 300,000 km/s, which is *faster than light in vacuum* and
    therefore understates achievable latency; it is not used here.
*   Single-mode fiber physics is ``c / n_group`` with ``n_group = 1.4682``
    (Corning SMF-28 at 1550 nm), i.e. ``4.897 us/km``. The default used for
    planning is the conservative industry convention of ``5.0 us/km``
    (200 km/ms), which is *slower* than physics and therefore safe: it never
    promises a latency the medium cannot deliver. Override per link via
    ``NetworkLinkSpec.propagation_us_per_km`` when a carrier publishes a
    measured figure.

Distances are **route** distances (actual cable / line-of-sight path), not
great-circle distances. Where only a straight-line distance is known, set
``route_circuity_factor`` to inflate it.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

# --- Physical constants -----------------------------------------------------
SPEED_OF_LIGHT_KM_S: float = 299_792.458          # exact, SI definition
REFRACTIVE_INDEX_AIR: float = 1.0003              # dry air, sea level
GROUP_INDEX_SMF28_1550NM: float = 1.4682          # Corning SMF-28 at 1550 nm

#: Absolute lower bound on propagation delay. Nothing may be modelled faster.
VACUUM_DELAY_US_KM: float = 1_000_000.0 / SPEED_OF_LIGHT_KM_S  # ~3.3356 us/km

#: Physics value for single-mode fiber (c / n_group), ~4.897 us/km.
PROPAGATION_PHYSICS_FIBER_US_KM: float = VACUUM_DELAY_US_KM * GROUP_INDEX_SMF28_1550NM

#: Conservative planning convention for fiber: 5.0 us/km == 200 km/ms.
PROPAGATION_SPEED_FIBER_US_KM: float = 5.0

#: Free-space RF (c / n_air), ~3.3366 us/km == ~5.37 us/mile.
PROPAGATION_SPEED_MICROWAVE_US_KM: float = VACUUM_DELAY_US_KM * REFRACTIVE_INDEX_AIR

MEDIUM_PROPAGATION_US_KM: Dict[str, float] = {
    "FIBER": PROPAGATION_SPEED_FIBER_US_KM,
    "MICROWAVE": PROPAGATION_SPEED_MICROWAVE_US_KM,
}

#: Published port-to-port latency anchors for switch_latency_us.
#: Arista 7150 (L2/3 ultra-low-latency): 350 ns. Arista 7130 (Layer 1): 4 ns.
DEFAULT_L2_SWITCH_LATENCY_US: float = 0.350
LAYER1_SWITCH_LATENCY_US: float = 0.004


def _require_finite(value: float, name: str) -> float:
    """Reject NaN/Inf before they silently propagate into a ranking."""
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{name} must be a finite number, got {value!r}")
    return numeric


def _require_non_negative(value: float, name: str) -> float:
    numeric = _require_finite(value, name)
    if numeric < 0.0:
        raise ValueError(f"{name} must be non-negative, got {numeric}")
    return numeric


def _require_non_empty(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string, got {value!r}")
    return value


@dataclass(frozen=True)
class FacilitySpec:
    """Monthly commercial terms for one candidate co-location facility."""

    name: str
    location_code: str          # e.g. 'NY4', 'AURORA', 'LD4'
    rack_cost_mrc: float        # Monthly recurring cost per rack ($)
    power_kw: float             # Committed power draw in kW
    power_cost_per_kw: float    # Cost per committed kW per month ($)
    cross_connect_mrc: float    # Cross-connect MRC ($)

    def __post_init__(self) -> None:
        _require_non_empty(self.name, "name")
        _require_non_empty(self.location_code, "location_code")
        for field_name in (
            "rack_cost_mrc",
            "power_kw",
            "power_cost_per_kw",
            "cross_connect_mrc",
        ):
            # Coerce as well as validate: numeric strings decoded from JSON
            # would otherwise pass validation and fail later in arithmetic.
            object.__setattr__(
                self, field_name,
                _require_non_negative(getattr(self, field_name), field_name),
            )


@dataclass(frozen=True)
class NetworkLinkSpec:
    """One path between two network endpoints.

    ``distance_km`` is the **route** distance. If only a great-circle distance
    is available, leave it as-is and raise ``route_circuity_factor``: fiber
    routes follow highway/rail rights-of-way and typically run 1.15-1.30x the
    straight line.
    """

    source: str
    destination: str
    distance_km: float
    medium_type: str                 # 'FIBER' or 'MICROWAVE'
    num_switches: int = 2            # Number of network switch hops
    switch_latency_us: float = DEFAULT_L2_SWITCH_LATENCY_US
    cross_connect_us: float = 0.0    # Aggregate patch / cross-connect delay
    route_circuity_factor: float = 1.0
    propagation_us_per_km: Optional[float] = None  # Carrier-measured override

    def __post_init__(self) -> None:
        _require_non_empty(self.source, "source")
        _require_non_empty(self.destination, "destination")
        # Coerce as well as validate: numeric strings decoded from JSON would
        # otherwise pass validation and fail later in arithmetic.
        for field_name in ("distance_km", "switch_latency_us", "cross_connect_us"):
            object.__setattr__(
                self, field_name,
                _require_non_negative(getattr(self, field_name), field_name),
            )

        if not isinstance(self.num_switches, int) or isinstance(self.num_switches, bool):
            raise ValueError(f"num_switches must be an int, got {self.num_switches!r}")
        if self.num_switches < 0:
            raise ValueError(f"num_switches must be non-negative, got {self.num_switches}")

        circuity = _require_finite(self.route_circuity_factor, "route_circuity_factor")
        if circuity < 1.0:
            raise ValueError(
                "route_circuity_factor must be >= 1.0 (a physical route cannot be "
                f"shorter than the straight line), got {circuity}"
            )
        object.__setattr__(self, "route_circuity_factor", circuity)

        if not isinstance(self.medium_type, str) or \
                self.medium_type.upper() not in MEDIUM_PROPAGATION_US_KM:
            raise ValueError(
                f"Unsupported medium type: {self.medium_type!r}. "
                f"Supported: {sorted(MEDIUM_PROPAGATION_US_KM)}"
            )

        if self.propagation_us_per_km is not None:
            override = _require_finite(self.propagation_us_per_km, "propagation_us_per_km")
            if override < VACUUM_DELAY_US_KM:
                raise ValueError(
                    f"propagation_us_per_km {override} us/km is faster than light in "
                    f"vacuum ({VACUUM_DELAY_US_KM:.6f} us/km) and is not physically "
                    "achievable"
                )
            object.__setattr__(self, "propagation_us_per_km", override)

    @property
    def effective_propagation_us_per_km(self) -> float:
        """Propagation delay constant actually applied to this link."""
        if self.propagation_us_per_km is not None:
            return float(self.propagation_us_per_km)
        return MEDIUM_PROPAGATION_US_KM[self.medium_type.upper()]


@dataclass(frozen=True)
class LatencyBudgetResult:
    """One-way and round-trip latency decomposition, in microseconds."""

    route_distance_km: float
    propagation_delay_us: float
    switch_delay_us: float
    cross_connect_delay_us: float
    total_one_way_us: float
    total_rtt_us: float


@dataclass(frozen=True)
class FacilityScore:
    """Ranked evaluation of one facility.

    ``composite_score`` is a **penalty**: lower is better. Facilities with no
    modelled link have ``avg_rtt_us is None`` and are left unranked rather than
    being credited with zero latency.
    """

    facility: str
    code: str
    tco_mrc: float
    num_links: int
    avg_rtt_us: Optional[float]
    normalized_latency: Optional[float]
    normalized_cost: Optional[float]
    composite_score: Optional[float]
    rank: Optional[int]


def _min_max_normalize(values: Sequence[float]) -> List[float]:
    """Scale to [0, 1] where 0 is best (lowest). Degenerate spread -> all 0.0."""
    lo, hi = min(values), max(values)
    spread = hi - lo
    if spread <= 0.0:
        return [0.0 for _ in values]
    return [(v - lo) / spread for v in values]


class ColocationTopologyEvaluator:
    """Quantitative framework for modelling cross-venue network topologies,
    decomposing latency budgets, and computing total cost of ownership (TCO).
    """

    @staticmethod
    def calculate_latency_budget(link: NetworkLinkSpec) -> LatencyBudgetResult:
        """Decompose a link into propagation, switching and cross-connect delay.

        ``total_rtt_us`` assumes a **symmetric** return path over the same
        medium. Cross-market topologies frequently send one direction over
        microwave and the other over fiber; use
        :meth:`calculate_round_trip_budget` for that case.
        """
        route_distance_km = link.distance_km * link.route_circuity_factor
        propagation_delay_us = route_distance_km * link.effective_propagation_us_per_km
        switch_delay_us = link.num_switches * link.switch_latency_us
        cross_connect_delay_us = link.cross_connect_us
        total_one_way_us = propagation_delay_us + switch_delay_us + cross_connect_delay_us
        # Guard against overflow to inf on absurd inputs reaching a ranking.
        _require_finite(total_one_way_us, "total_one_way_us")

        logger.debug(
            "latency_budget source=%s destination=%s medium=%s route_km=%.3f "
            "one_way_us=%.3f",
            link.source,
            link.destination,
            link.medium_type.upper(),
            route_distance_km,
            total_one_way_us,
        )

        return LatencyBudgetResult(
            route_distance_km=round(route_distance_km, 6),
            propagation_delay_us=round(propagation_delay_us, 3),
            switch_delay_us=round(switch_delay_us, 3),
            cross_connect_delay_us=round(cross_connect_delay_us, 3),
            total_one_way_us=round(total_one_way_us, 3),
            total_rtt_us=round(total_one_way_us * 2.0, 3),
        )

    @classmethod
    def calculate_round_trip_budget(
        cls, outbound: NetworkLinkSpec, inbound: NetworkLinkSpec
    ) -> float:
        """Round-trip microseconds over an explicitly asymmetric path."""
        out_us = cls.calculate_latency_budget(outbound).total_one_way_us
        back_us = cls.calculate_latency_budget(inbound).total_one_way_us
        return round(out_us + back_us, 3)

    @staticmethod
    def calculate_facility_tco(facility: FacilitySpec) -> float:
        """Monthly total cost of ownership ($) for one facility footprint."""
        total_power_cost = facility.power_kw * facility.power_cost_per_kw
        total_tco = facility.rack_cost_mrc + total_power_cost + facility.cross_connect_mrc
        return round(total_tco, 2)

    def evaluate_colocation_setup(
        self,
        facilities: Sequence[FacilitySpec],
        links: Sequence[NetworkLinkSpec],
        latency_weight: float = 0.7,
        cost_weight: float = 0.3,
    ) -> List[FacilityScore]:
        """Rank facilities by a weighted latency/cost penalty score.

        Latency and TCO are min-max normalized across the *rankable* candidates
        (those with at least one modelled link), so the score is a relative
        comparison within the supplied candidate set and is not comparable
        across different candidate sets.

        Weights are rescaled to sum to 1.0. Results are returned in rank order
        (best first); unrankable facilities are appended, sorted by name, with
        ``rank=None``.

        Raises:
            ValueError: on non-finite or negative weights, zero total weight, or
                duplicate ``location_code`` values (which make link-to-facility
                attribution ambiguous).
        """
        w_lat = _require_non_negative(latency_weight, "latency_weight")
        w_cost = _require_non_negative(cost_weight, "cost_weight")
        weight_sum = w_lat + w_cost
        if weight_sum <= 0.0:
            raise ValueError("latency_weight + cost_weight must be > 0")
        w_lat, w_cost = w_lat / weight_sum, w_cost / weight_sum

        codes = [f.location_code for f in facilities]
        duplicates = sorted({c for c in codes if codes.count(c) > 1})
        if duplicates:
            raise ValueError(f"Duplicate facility location_code(s): {duplicates}")

        rankable: List[FacilityScore] = []
        unrankable: List[FacilityScore] = []
        for fac in facilities:
            fac_links = [
                link for link in links
                if fac.location_code in (link.source, link.destination)
            ]
            tco = self.calculate_facility_tco(fac)
            if fac_links:
                rtts = [
                    self.calculate_latency_budget(link).total_rtt_us for link in fac_links
                ]
                rankable.append(FacilityScore(
                    facility=fac.name,
                    code=fac.location_code,
                    tco_mrc=tco,
                    num_links=len(fac_links),
                    avg_rtt_us=round(sum(rtts) / len(rtts), 3),
                    normalized_latency=None,
                    normalized_cost=None,
                    composite_score=None,
                    rank=None,
                ))
            else:
                logger.warning(
                    "Facility %s (%s) has no modelled network link; reporting it "
                    "unranked rather than crediting it with zero latency.",
                    fac.name,
                    fac.location_code,
                )
                unrankable.append(FacilityScore(
                    facility=fac.name,
                    code=fac.location_code,
                    tco_mrc=tco,
                    num_links=0,
                    avg_rtt_us=None,
                    normalized_latency=None,
                    normalized_cost=None,
                    composite_score=None,
                    rank=None,
                ))

        scored: List[FacilityScore] = []
        if rankable:
            norm_lat = _min_max_normalize([float(s.avg_rtt_us) for s in rankable])
            norm_cost = _min_max_normalize([s.tco_mrc for s in rankable])
            scored = [
                replace(
                    entry,
                    normalized_latency=round(nl, 6),
                    normalized_cost=round(nc, 6),
                    composite_score=round(w_lat * nl + w_cost * nc, 6),
                )
                for entry, nl, nc in zip(rankable, norm_lat, norm_cost)
            ]
            # Deterministic order; equal scores share a rank (competition ranking).
            scored.sort(key=lambda s: (s.composite_score, s.facility))
            previous_score: Optional[float] = None
            current_rank = 0
            ranked: List[FacilityScore] = []
            for position, entry in enumerate(scored, start=1):
                if previous_score is None or entry.composite_score != previous_score:
                    current_rank = position
                    previous_score = entry.composite_score
                ranked.append(replace(entry, rank=current_rank))
            scored = ranked

        scored.extend(sorted(unrankable, key=lambda s: s.facility))
        return scored
