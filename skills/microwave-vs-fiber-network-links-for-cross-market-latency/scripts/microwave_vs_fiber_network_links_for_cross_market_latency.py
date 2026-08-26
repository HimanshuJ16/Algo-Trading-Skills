"""Microwave-vs-fiber link arbitration for cross-market latency corridors.

This module answers two separate questions and keeps them separate on purpose:

1.  **How much faster is the radio path?** Propagation delay is computed from
    ``distance / (c / n)`` for each medium, plus the *equipment* terms that a
    propagation-only model omits. Radio corridors are chains of repeater towers
    (~22 on the shortest reconstructed Chicago-NJ path) and the last hop into
    each data centre is fiber, not air. Bhattacherjee et al. rank five competing
    Chicago-NJ networks within 0.4-8.1 us of each other and note explicitly that
    "if both NLN and JM were using the same radios, and the per-tower added
    latency was higher than 1.4 us, JM would offer lower end-end latency" --
    i.e. the term this model used to drop is large enough to invert the ranking.
    When no equipment terms are supplied the report says so
    (``is_microwave_lower_bound_only``) instead of presenting a floor as an
    estimate.

2.  **Which link should carry traffic right now?** Radio links fail in weather.
    The trade-off is not marginal: McKay Brothers' co-founder said their
    microwave network "was down 1 per cent of the time during trading hours in
    December and January", against Spread Networks' claimed 99.999% fiber
    availability. Arbitration therefore fails *closed* -- unknown weather
    states, stale telemetry and a simultaneously-unhealthy backup all produce an
    explicit status rather than defaulting onto the fragile link.

Propagation constants
---------------------
``c = 299_792.458 km/s`` exactly (SI definition of the metre). Nothing may be
modelled faster.

*   **Air (radio).** ITU-R P.453-14 gives an average sea-level radio
    refractivity of ``N_0 = 315`` N-units, i.e. ``n_air = 1.000315`` and
    ``c/n = 299_698.05 km/s`` (3.336692 us/km). This is the *radio* refractive
    index, which carries a water-vapour term absent from the optical index of
    air (1.000273); the two differ by 0.06 us over a 1,186 km corridor, far
    below the equipment terms that dominate.
*   **Fiber.** ``n_group = 1.4682`` (Corning SMF-28 effective group index at
    1550 nm) gives ``204_190.48 km/s`` (4.897388 us/km). This is the default
    because it is the *fastest* of the common single-mode types and therefore
    the conservative choice when quoting a microwave advantage. Per Bozkurt et
    al. (citing Corning WP8080), ITU-T G.655 NZ-DSF runs at n = 1.470
    (203_940 km/s) and ultra-low-latency fiber at n = 1.462 (205_056 km/s).
    Over a 1,328 km corridor the G.652-vs-G.655 choice alone is ~8 us one-way --
    larger than the margin separating competing radio networks. Set
    ``propagation_speed_km_s`` per link rather than accepting a default.

Scope
-----
This module is a stateless evaluator. It does not read a clock, poll a weather
feed, or move traffic; it audits inputs the caller has already collected and
returns a routing verdict. Round-trip figures assume a **symmetric** path; for
the common microwave-out/fiber-back topology, budget each leg separately with
``co-location-provider-selection-and-network-topology``.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import FrozenSet, List, Optional, Sequence

logger = logging.getLogger(__name__)

# --- Physical constants -----------------------------------------------------
#: Exact, SI definition of the metre. Nothing may be modelled faster than this.
SPEED_OF_LIGHT_VACUUM_KM_S: float = 299_792.458

#: ITU-R P.453-14: average sea-level radio refractivity N_0 = 315 N-units.
RADIO_REFRACTIVITY_N_UNITS: float = 315.0
REFRACTIVE_INDEX_AIR_RADIO: float = 1.0 + RADIO_REFRACTIVITY_N_UNITS * 1e-6

#: Corning SMF-28 effective group index of refraction at 1550 nm (ITU-T G.652).
GROUP_INDEX_SMF28_1550NM: float = 1.4682

#: Microwave / millimetre-wave line-of-sight propagation, ~299,698 km/s.
SPEED_OF_LIGHT_AIR_KM_S: float = SPEED_OF_LIGHT_VACUUM_KM_S / REFRACTIVE_INDEX_AIR_RADIO

#: Single-mode fiber propagation (c / n_group), ~204,190 km/s.
SPEED_OF_LIGHT_FIBER_KM_S: float = SPEED_OF_LIGHT_VACUUM_KM_S / GROUP_INDEX_SMF28_1550NM

# --- Link types -------------------------------------------------------------
LINK_TYPE_MICROWAVE = "MICROWAVE"
LINK_TYPE_FIBER = "FIBER"
LINK_TYPES: FrozenSet[str] = frozenset({LINK_TYPE_MICROWAVE, LINK_TYPE_FIBER})

DEFAULT_PROPAGATION_SPEED_KM_S = {
    LINK_TYPE_MICROWAVE: SPEED_OF_LIGHT_AIR_KM_S,
    LINK_TYPE_FIBER: SPEED_OF_LIGHT_FIBER_KM_S,
}

# --- Weather states ---------------------------------------------------------
#: Weather states this module recognises. Anything outside this set is treated
#: as unusable telemetry, never as "fine". Operators whose weather feed uses a
#: different vocabulary should map onto these or pass their own sets to the
#: engine -- silently accepting an unknown string is how a feed schema change
#: turns into unnoticed routing over a degraded link.
DEFAULT_KNOWN_WEATHER_STATES: FrozenSet[str] = frozenset(
    {"CLEAR", "LIGHT_RAIN", "MODERATE_RAIN", "HEAVY_RAIN", "STORM", "SNOW", "FOG"}
)

#: States that force failover on their own. Confined to heavy liquid
#: precipitation: ITU-R P.530 notes rain attenuation "can be ignored at
#: frequencies below about 5 GHz, but must be included in design calculations at
#: higher frequencies, where its importance increases rapidly". Dry snow and fog
#: are weak attenuators in the 6-11 GHz bands these corridors use, so they are
#: left to be caught by the SNR and packet-loss signals rather than assumed
#: benign *or* assumed fatal.
DEFAULT_DEGRADING_WEATHER_STATES: FrozenSet[str] = frozenset({"HEAVY_RAIN", "STORM"})

#: ITU-R P.530: below this frequency rain attenuation is negligible for
#: terrestrial line-of-sight design.
RAIN_ATTENUATION_NEGLIGIBLE_BELOW_GHZ: float = 5.0

# --- Arbitration statuses ---------------------------------------------------
STATUS_MICROWAVE_PRIMARY = "ROUTE_MICROWAVE_PRIMARY"
STATUS_FAILOVER_RAIN_FADE = "FAILOVER_TO_FIBER_RAIN_FADE"
STATUS_FAILOVER_TELEMETRY_UNUSABLE = "FAILOVER_TO_FIBER_TELEMETRY_UNUSABLE"
STATUS_HOLD_FIBER_HYSTERESIS = "HOLD_FIBER_RECOVERY_HYSTERESIS"
STATUS_NO_HEALTHY_LINK = "NO_HEALTHY_LINK_ESCALATE"

#: Every status the engine can return.
ALL_STATUSES: FrozenSet[str] = frozenset(
    {
        STATUS_MICROWAVE_PRIMARY,
        STATUS_FAILOVER_RAIN_FADE,
        STATUS_FAILOVER_TELEMETRY_UNUSABLE,
        STATUS_HOLD_FIBER_HYSTERESIS,
        STATUS_NO_HEALTHY_LINK,
    }
)

#: Statuses in which traffic is on (or held on) the fiber path.
FIBER_ROUTED_STATUSES: FrozenSet[str] = frozenset(
    {STATUS_FAILOVER_RAIN_FADE, STATUS_FAILOVER_TELEMETRY_UNUSABLE, STATUS_HOLD_FIBER_HYSTERESIS}
)

#: A distance larger than this is a unit error, not a link: the equatorial
#: circumference is ~40,075 km, so no terrestrial path exceeds half of it by
#: much even with extreme circuity.
MAX_PLAUSIBLE_DISTANCE_KM: float = 100_000.0


class NetworkLinkError(ValueError):
    """Raised when a link configuration or telemetry sample cannot be arbitrated.

    Subclasses ``ValueError`` so callers written against the previous
    ``raise ValueError`` on a non-positive distance keep working unchanged.
    """


def _require_finite(value: float, name: str) -> float:
    """Return ``value`` as a float, rejecting NaN, Inf and non-numerics.

    NaN is rejected rather than propagated because every comparison against a
    NaN is ``False``: a NaN distance yields a NaN RTT, a NaN packet loss never
    exceeds any threshold, and the report renders as a clean primary route.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NetworkLinkError(f"{name} must be a real number, got {value!r}.")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise NetworkLinkError(f"{name} must be finite, got {numeric!r}.")
    return numeric


def _require_non_negative(value: float, name: str) -> float:
    numeric = _require_finite(value, name)
    if numeric < 0.0:
        raise NetworkLinkError(f"{name} must be non-negative, got {numeric}.")
    return numeric


def serialization_delay_us(payload_bytes: int, bandwidth_mbps: float) -> float:
    """Return the one-way time to clock ``payload_bytes`` onto the link, in us.

    Serialization is the reason link *bandwidth* shows up in a *latency* budget,
    and it is where the microwave/fiber trade-off reverses. Radio channels are
    narrow -- FCC Part 101 coordinates up to 60 MHz at 6 GHz and 80 MHz at
    11 GHz -- so a link running at 100 Mbps spends 120 us clocking out a single
    1,500-byte frame. That is ~3% of the entire 4 ms one-way corridor budget and
    an order of magnitude more than the 0.4-8.1 us that separates competing
    radio networks. The same frame on a 10 Gbps fiber costs 1.2 us. This is why
    radio corridors carry a curated subset of the data and the bulk feed stays
    on fiber.
    """
    payload = _require_non_negative(payload_bytes, "payload_bytes")
    bandwidth = _require_finite(bandwidth_mbps, "bandwidth_mbps")
    if bandwidth <= 0.0:
        raise NetworkLinkError(f"bandwidth_mbps must be strictly positive, got {bandwidth}.")
    return (payload * 8.0) / bandwidth


@dataclass
class NetworkLinkConfig:
    """Physical configuration of one leg of a cross-market corridor.

    ``distance_km`` is the **route** distance actually travelled, not the
    great-circle distance. For the Chicago-NJ corridor the CME-Carteret geodesic
    is ~1,176-1,186 km, while Spread Networks' fiber is 1,328 km of glass:
    Bozkurt et al. measured its ground path at 1,253 km and attribute the
    remaining ~6% to slack coils left for future repairs. Feeding a geodesic
    into a fiber budget understates it by roughly that whole 12%.

    ``repeater_count`` and ``per_repeater_latency_us`` carry the equipment term.
    Leave them at zero only if you accept that the result is a floor: the report
    flags it as such.
    """

    link_id: str
    link_type: str
    corridor_name: str
    distance_km: float
    bandwidth_mbps: float = 1000.0
    #: Overrides the medium default. Set it from the carrier's published or
    #: measured figure, or from the fiber type actually lit on the route.
    propagation_speed_km_s: Optional[float] = None
    #: Radio repeater towers, or in-line optical amplifiers / regenerators.
    repeater_count: int = 0
    #: Added latency per repeater, from the radio or amplifier datasheet.
    per_repeater_latency_us: float = 0.0
    #: Fiber between the data centres and the first/last radio tower, summed
    #: across both ends. Radio corridors are hybrid: the "microwave" path is
    #: only air between towers.
    fiber_tail_km: float = 0.0
    #: Operating frequency, used to report weather sensitivity. Chicago-NJ
    #: networks concentrate in the 6 GHz and 11 GHz FCC Part 101 bands.
    frequency_ghz: Optional[float] = None
    #: Payload whose serialization delay to include. ``None`` excludes the term.
    payload_bytes: Optional[int] = None

    def __post_init__(self) -> None:
        if not str(self.link_id).strip():
            raise NetworkLinkError("link_id must be a non-empty string.")
        self.link_type = str(self.link_type).strip().upper()
        if self.link_type not in LINK_TYPES:
            raise NetworkLinkError(
                f"link_type must be one of {sorted(LINK_TYPES)}, got {self.link_type!r}."
            )
        if not str(self.corridor_name).strip():
            raise NetworkLinkError("corridor_name must be a non-empty string.")

        self.distance_km = _require_finite(self.distance_km, "distance_km")
        if self.distance_km <= 0.0:
            raise NetworkLinkError(
                f"distance_km must be strictly positive, got {self.distance_km}."
            )
        if self.distance_km > MAX_PLAUSIBLE_DISTANCE_KM:
            raise NetworkLinkError(
                f"distance_km of {self.distance_km} km exceeds the plausible bound of "
                f"{MAX_PLAUSIBLE_DISTANCE_KM} km -- check the unit."
            )

        self.bandwidth_mbps = _require_finite(self.bandwidth_mbps, "bandwidth_mbps")
        if self.bandwidth_mbps <= 0.0:
            raise NetworkLinkError(
                f"bandwidth_mbps must be strictly positive, got {self.bandwidth_mbps}."
            )

        if self.propagation_speed_km_s is None:
            self.propagation_speed_km_s = DEFAULT_PROPAGATION_SPEED_KM_S[self.link_type]
        else:
            self.propagation_speed_km_s = _require_finite(
                self.propagation_speed_km_s, "propagation_speed_km_s"
            )
            if self.propagation_speed_km_s <= 0.0:
                raise NetworkLinkError(
                    f"propagation_speed_km_s must be strictly positive, "
                    f"got {self.propagation_speed_km_s}."
                )
            if self.propagation_speed_km_s > SPEED_OF_LIGHT_VACUUM_KM_S:
                raise NetworkLinkError(
                    f"propagation_speed_km_s of {self.propagation_speed_km_s} km/s exceeds c "
                    f"({SPEED_OF_LIGHT_VACUUM_KM_S} km/s). The '300,000 km/s' shorthand is "
                    f"superluminal and flatters every radio budget that uses it."
                )

        if isinstance(self.repeater_count, bool) or not isinstance(self.repeater_count, int):
            raise NetworkLinkError(f"repeater_count must be an int, got {self.repeater_count!r}.")
        if self.repeater_count < 0:
            raise NetworkLinkError(f"repeater_count must be non-negative, got {self.repeater_count}.")
        self.per_repeater_latency_us = _require_non_negative(
            self.per_repeater_latency_us, "per_repeater_latency_us"
        )
        self.fiber_tail_km = _require_non_negative(self.fiber_tail_km, "fiber_tail_km")

        if self.frequency_ghz is not None:
            self.frequency_ghz = _require_finite(self.frequency_ghz, "frequency_ghz")
            if self.frequency_ghz <= 0.0:
                raise NetworkLinkError(
                    f"frequency_ghz must be strictly positive, got {self.frequency_ghz}."
                )
        if self.payload_bytes is not None:
            payload = _require_non_negative(self.payload_bytes, "payload_bytes")
            self.payload_bytes = int(payload)

    @property
    def has_equipment_model(self) -> bool:
        """True when the config carries a non-zero equipment or fiber-tail term."""
        return (
            self.repeater_count > 0 and self.per_repeater_latency_us > 0.0
        ) or self.fiber_tail_km > 0.0

    def propagation_one_way_ms(self) -> float:
        """Medium propagation only, including any fiber tail. A hard floor."""
        speed = float(self.propagation_speed_km_s)
        air_or_glass_ms = (self.distance_km / speed) * 1000.0
        tail_ms = (self.fiber_tail_km / SPEED_OF_LIGHT_FIBER_KM_S) * 1000.0
        return air_or_glass_ms + tail_ms

    def equipment_one_way_ms(self) -> float:
        """Repeater and serialization delay, in ms. Zero when unmodelled."""
        repeater_ms = (self.repeater_count * self.per_repeater_latency_us) / 1000.0
        serialization_ms = 0.0
        if self.payload_bytes is not None:
            serialization_ms = (
                serialization_delay_us(self.payload_bytes, self.bandwidth_mbps) / 1000.0
            )
        return repeater_ms + serialization_ms

    def one_way_latency_ms(self) -> float:
        """Total modelled one-way latency: propagation plus equipment."""
        return self.propagation_one_way_ms() + self.equipment_one_way_ms()

    def round_trip_latency_ms(self) -> float:
        """Round trip, valid only for a symmetric path (see the module docstring)."""
        return 2.0 * self.one_way_latency_ms()


@dataclass
class WeatherLinkTelemetry:
    """Current observed state of one link.

    ``signal_to_noise_ratio_db`` is the leading indicator: a fading radio link
    loses SNR, drops to a more robust modulation, and only then starts losing
    packets. Supply ``min_snr_db`` on the engine to make it actionable --
    the threshold is your radio's modulation-specific demodulation floor plus
    the fade margin from the link budget, and this module does not invent one.
    """

    link_id: str
    current_weather: str
    packet_loss_pct: float
    signal_to_noise_ratio_db: float
    #: Age of this observation. Checked only when ``max_telemetry_age_s`` is set.
    telemetry_age_s: Optional[float] = None

    def __post_init__(self) -> None:
        if not str(self.link_id).strip():
            raise NetworkLinkError("link_id must be a non-empty string.")
        self.current_weather = str(self.current_weather).strip().upper()
        self.packet_loss_pct = _require_finite(self.packet_loss_pct, "packet_loss_pct")
        if not 0.0 <= self.packet_loss_pct <= 100.0:
            raise NetworkLinkError(
                f"packet_loss_pct must be within [0, 100], got {self.packet_loss_pct}."
            )
        self.signal_to_noise_ratio_db = _require_finite(
            self.signal_to_noise_ratio_db, "signal_to_noise_ratio_db"
        )
        if self.telemetry_age_s is not None:
            self.telemetry_age_s = _require_non_negative(self.telemetry_age_s, "telemetry_age_s")


@dataclass
class NetworkLinkReport:
    """Arbitration verdict plus the latency decomposition that justifies it."""

    corridor_name: str
    microwave_rtt_ms: float
    fiber_rtt_ms: float
    latency_savings_ms: float
    latency_advantage_pct: float
    selected_routing_link_id: str
    selected_link_type: str
    is_failover_active: bool
    status: str
    audit_notes: str
    # --- Decomposition -----------------------------------------------------
    microwave_one_way_ms: float = 0.0
    fiber_one_way_ms: float = 0.0
    microwave_propagation_one_way_ms: float = 0.0
    fiber_propagation_one_way_ms: float = 0.0
    microwave_equipment_one_way_ms: float = 0.0
    fiber_equipment_one_way_ms: float = 0.0
    #: True when the microwave figure omits every equipment term and is a floor
    #: no real link achieves, not an estimate of what this one does.
    is_microwave_lower_bound_only: bool = True
    is_fiber_lower_bound_only: bool = True
    #: Named degradation signals that fired, in the order they were evaluated.
    degradation_reasons: List[str] = field(default_factory=list)
    #: Consecutive clean evaluations carried forward for recovery hysteresis.
    consecutive_clean_evaluations: int = 0
    weather_sensitivity_note: str = ""


class NetworkLinkArbitratorEngine:
    """Selects between a primary radio link and a fiber backup for one corridor.

    The engine is stateless: recovery hysteresis is driven by
    ``previous_status`` and ``consecutive_clean_evaluations`` passed in by the
    caller, so the same inputs always produce the same verdict and no hidden
    state accumulates between corridors.

    Thresholds are asymmetric by design. ``failover_packet_loss_pct`` (default
    1.0) trips the failover; ``recovery_packet_loss_pct`` (default 0.1) must be
    met before returning to radio, and ``recovery_dwell_evaluations`` (default
    3) clean evaluations must accumulate first. A single threshold flaps: loss
    oscillating either side of 1.0% would swap the route on every evaluation,
    and each swap reorders packets in flight on a path whose entire competitive
    margin is single-digit microseconds.
    """

    def __init__(
        self,
        max_packet_loss_threshold_pct: float = 1.0,
        recovery_packet_loss_pct: float = 0.1,
        recovery_dwell_evaluations: int = 3,
        min_snr_db: Optional[float] = None,
        max_telemetry_age_s: Optional[float] = None,
        known_weather_states: Optional[Sequence[str]] = None,
        degrading_weather_states: Optional[Sequence[str]] = None,
    ) -> None:
        self.max_packet_loss_threshold_pct = _require_finite(
            max_packet_loss_threshold_pct, "max_packet_loss_threshold_pct"
        )
        if not 0.0 <= self.max_packet_loss_threshold_pct <= 100.0:
            raise NetworkLinkError(
                "max_packet_loss_threshold_pct must be within [0, 100], got "
                f"{self.max_packet_loss_threshold_pct}."
            )
        self.recovery_packet_loss_pct = _require_finite(
            recovery_packet_loss_pct, "recovery_packet_loss_pct"
        )
        strictly_below = self.recovery_packet_loss_pct < self.max_packet_loss_threshold_pct
        both_zero = (
            self.recovery_packet_loss_pct == 0.0 and self.max_packet_loss_threshold_pct == 0.0
        )
        if self.recovery_packet_loss_pct < 0.0 or not (strictly_below or both_zero):
            raise NetworkLinkError(
                "recovery_packet_loss_pct must be non-negative and strictly below "
                f"max_packet_loss_threshold_pct; got {self.recovery_packet_loss_pct} against "
                f"{self.max_packet_loss_threshold_pct}. A recovery threshold at or above the "
                "failover threshold leaves no hysteresis band, and the route flaps on every "
                "oscillation across the single shared threshold."
            )
        if isinstance(recovery_dwell_evaluations, bool) or not isinstance(
            recovery_dwell_evaluations, int
        ):
            raise NetworkLinkError("recovery_dwell_evaluations must be an int.")
        if recovery_dwell_evaluations < 1:
            raise NetworkLinkError(
                f"recovery_dwell_evaluations must be >= 1, got {recovery_dwell_evaluations}."
            )
        self.recovery_dwell_evaluations = recovery_dwell_evaluations

        self.min_snr_db = (
            None if min_snr_db is None else _require_finite(min_snr_db, "min_snr_db")
        )
        self.max_telemetry_age_s = (
            None
            if max_telemetry_age_s is None
            else _require_non_negative(max_telemetry_age_s, "max_telemetry_age_s")
        )
        self.known_weather_states = frozenset(
            s.strip().upper() for s in (known_weather_states or DEFAULT_KNOWN_WEATHER_STATES)
        )
        self.degrading_weather_states = frozenset(
            s.strip().upper()
            for s in (degrading_weather_states or DEFAULT_DEGRADING_WEATHER_STATES)
        )
        unknown_degrading = self.degrading_weather_states - self.known_weather_states
        if unknown_degrading:
            raise NetworkLinkError(
                f"degrading_weather_states contains states absent from known_weather_states: "
                f"{sorted(unknown_degrading)}."
            )

    # --- Degradation signals ------------------------------------------------
    def _telemetry_unusable_reasons(self, telemetry: WeatherLinkTelemetry) -> List[str]:
        """Signals meaning the telemetry cannot be trusted, so routing fails closed."""
        reasons: List[str] = []
        if telemetry.current_weather not in self.known_weather_states:
            reasons.append(
                f"UNKNOWN_WEATHER_STATE:{telemetry.current_weather!r} "
                f"(known: {sorted(self.known_weather_states)})"
            )
        if (
            self.max_telemetry_age_s is not None
            and telemetry.telemetry_age_s is not None
            and telemetry.telemetry_age_s > self.max_telemetry_age_s
        ):
            reasons.append(
                f"STALE_TELEMETRY:{telemetry.telemetry_age_s:.1f}s > "
                f"{self.max_telemetry_age_s:.1f}s"
            )
        return reasons

    def _degradation_reasons(self, telemetry: WeatherLinkTelemetry) -> List[str]:
        """Signals meaning the link is measurably degraded right now."""
        reasons: List[str] = []
        if telemetry.current_weather in self.degrading_weather_states:
            reasons.append(f"DEGRADING_WEATHER:{telemetry.current_weather}")
        if telemetry.packet_loss_pct > self.max_packet_loss_threshold_pct:
            # Rendered with significant digits, not fixed decimals: a marginal
            # breach formatted to 3 dp reads as "1.000% > 1.000%", which looks
            # like a contradiction to whoever is holding the pager.
            reasons.append(
                f"PACKET_LOSS:{telemetry.packet_loss_pct:.6g}% > "
                f"{self.max_packet_loss_threshold_pct:.6g}%"
            )
        if self.min_snr_db is not None and telemetry.signal_to_noise_ratio_db < self.min_snr_db:
            reasons.append(
                f"SNR_BELOW_MARGIN:{telemetry.signal_to_noise_ratio_db:.1f}dB < "
                f"{self.min_snr_db:.1f}dB"
            )
        return reasons

    def _weather_sensitivity_note(self, cfg: NetworkLinkConfig) -> str:
        """Describe how weather-exposed this radio path is, from its frequency."""
        if cfg.frequency_ghz is None:
            return (
                "frequency_ghz not set: weather sensitivity unknown. Rain attenuation is "
                "strongly frequency-dependent (ITU-R P.530)."
            )
        if cfg.frequency_ghz < RAIN_ATTENUATION_NEGLIGIBLE_BELOW_GHZ:
            return (
                f"{cfg.frequency_ghz:.1f} GHz is below the ~5 GHz threshold at which ITU-R P.530 "
                "says rain attenuation may be ignored for terrestrial line-of-sight design."
            )
        return (
            f"{cfg.frequency_ghz:.1f} GHz is above ~5 GHz, where ITU-R P.530 requires rain "
            "attenuation in the link budget and its importance 'increases rapidly' with "
            "frequency. Chicago-NJ networks that favour 6 GHz over 11 GHz trade a few "
            "microseconds of path latency for materially better wet-weather availability."
        )

    # --- Arbitration --------------------------------------------------------
    def arbitrate_cross_market_links(
        self,
        microwave_cfg: NetworkLinkConfig,
        fiber_cfg: NetworkLinkConfig,
        microwave_telemetry: WeatherLinkTelemetry,
        fiber_telemetry: Optional[WeatherLinkTelemetry] = None,
        previous_status: Optional[str] = None,
        consecutive_clean_evaluations: int = 0,
    ) -> NetworkLinkReport:
        """Compute the latency decomposition and choose the link to route over.

        ``fiber_telemetry`` is optional but strongly recommended: without it the
        backup is assumed healthy, and a failover into a dead backup is
        indistinguishable from a successful one. When both links are degraded
        the verdict is ``NO_HEALTHY_LINK_ESCALATE`` -- there is no safe route,
        and saying so is more useful than nominating one anyway.

        Status precedence:
        ``NO_HEALTHY_LINK_ESCALATE`` > ``FAILOVER_TO_FIBER_RAIN_FADE`` >
        ``FAILOVER_TO_FIBER_TELEMETRY_UNUSABLE`` >
        ``HOLD_FIBER_RECOVERY_HYSTERESIS`` > ``ROUTE_MICROWAVE_PRIMARY``.
        """
        if microwave_cfg.link_type != LINK_TYPE_MICROWAVE:
            raise NetworkLinkError(
                f"microwave_cfg must have link_type {LINK_TYPE_MICROWAVE!r}, got "
                f"{microwave_cfg.link_type!r}. Swapping the arguments would compute the fiber "
                f"path at the speed of light in air."
            )
        if fiber_cfg.link_type != LINK_TYPE_FIBER:
            raise NetworkLinkError(
                f"fiber_cfg must have link_type {LINK_TYPE_FIBER!r}, got {fiber_cfg.link_type!r}."
            )
        if microwave_telemetry.link_id != microwave_cfg.link_id:
            raise NetworkLinkError(
                f"microwave_telemetry.link_id {microwave_telemetry.link_id!r} does not match "
                f"microwave_cfg.link_id {microwave_cfg.link_id!r}. Arbitrating on another "
                f"link's telemetry silently routes on the wrong evidence."
            )
        if fiber_telemetry is not None and fiber_telemetry.link_id != fiber_cfg.link_id:
            raise NetworkLinkError(
                f"fiber_telemetry.link_id {fiber_telemetry.link_id!r} does not match "
                f"fiber_cfg.link_id {fiber_cfg.link_id!r}."
            )
        if microwave_cfg.link_id == fiber_cfg.link_id:
            raise NetworkLinkError(
                f"microwave_cfg and fiber_cfg share link_id {microwave_cfg.link_id!r}; "
                f"selected_routing_link_id would not identify which path was chosen."
            )
        if previous_status is not None and previous_status not in ALL_STATUSES:
            raise NetworkLinkError(
                f"previous_status must be one of {sorted(ALL_STATUSES)} or None, got "
                f"{previous_status!r}. An unrecognised value would silently skip recovery "
                f"hysteresis and snap the route back to microwave."
            )
        if isinstance(consecutive_clean_evaluations, bool) or not isinstance(
            consecutive_clean_evaluations, int
        ):
            raise NetworkLinkError("consecutive_clean_evaluations must be an int.")
        if consecutive_clean_evaluations < 0:
            raise NetworkLinkError(
                f"consecutive_clean_evaluations must be non-negative, "
                f"got {consecutive_clean_evaluations}."
            )

        # 1. Latency decomposition. Comparisons run on unrounded values; the
        #    rounding below is applied to the report fields only.
        micro_prop_ms = microwave_cfg.propagation_one_way_ms()
        micro_equip_ms = microwave_cfg.equipment_one_way_ms()
        micro_one_way_ms = micro_prop_ms + micro_equip_ms
        micro_rtt_ms = 2.0 * micro_one_way_ms

        fiber_prop_ms = fiber_cfg.propagation_one_way_ms()
        fiber_equip_ms = fiber_cfg.equipment_one_way_ms()
        fiber_one_way_ms = fiber_prop_ms + fiber_equip_ms
        fiber_rtt_ms = 2.0 * fiber_one_way_ms

        savings_ms = fiber_rtt_ms - micro_rtt_ms
        advantage_pct = (savings_ms / fiber_rtt_ms) * 100.0

        # 2. Degradation audit.
        micro_unusable = self._telemetry_unusable_reasons(microwave_telemetry)
        micro_degraded = self._degradation_reasons(microwave_telemetry)
        microwave_unhealthy = bool(micro_unusable or micro_degraded)

        fiber_unhealthy = False
        fiber_reasons: List[str] = []
        if fiber_telemetry is not None:
            fiber_reasons = self._telemetry_unusable_reasons(
                fiber_telemetry
            ) + self._degradation_reasons(fiber_telemetry)
            fiber_unhealthy = bool(fiber_reasons)

        reasons = [f"MICROWAVE:{r}" for r in micro_unusable + micro_degraded]
        reasons += [f"FIBER:{r}" for r in fiber_reasons]

        # 3. Status resolution, highest precedence first.
        clean_count = consecutive_clean_evaluations
        was_on_fiber = previous_status in FIBER_ROUTED_STATUSES

        if microwave_unhealthy and fiber_unhealthy:
            status = STATUS_NO_HEALTHY_LINK
            clean_count = 0
        elif micro_degraded:
            status = STATUS_FAILOVER_RAIN_FADE
            clean_count = 0
        elif micro_unusable:
            status = STATUS_FAILOVER_TELEMETRY_UNUSABLE
            clean_count = 0
        else:
            # Microwave telemetry is usable and nothing tripped the failover
            # thresholds. Returning to radio additionally requires clearing the
            # tighter recovery threshold for the full dwell.
            meets_recovery = (
                microwave_telemetry.packet_loss_pct <= self.recovery_packet_loss_pct
            )
            clean_count = clean_count + 1 if meets_recovery else 0
            if was_on_fiber and clean_count < self.recovery_dwell_evaluations:
                status = STATUS_HOLD_FIBER_HYSTERESIS
            else:
                status = STATUS_MICROWAVE_PRIMARY

        # 4. Route selection.
        if status == STATUS_MICROWAVE_PRIMARY:
            selected_link_id = microwave_cfg.link_id
            selected_type = LINK_TYPE_MICROWAVE
        elif status == STATUS_NO_HEALTHY_LINK:
            selected_link_id = ""
            selected_type = "NONE"
        else:
            selected_link_id = fiber_cfg.link_id
            selected_type = LINK_TYPE_FIBER

        notes = self._build_notes(
            status=status,
            microwave_cfg=microwave_cfg,
            fiber_cfg=fiber_cfg,
            micro_rtt_ms=micro_rtt_ms,
            fiber_rtt_ms=fiber_rtt_ms,
            savings_ms=savings_ms,
            advantage_pct=advantage_pct,
            reasons=reasons,
            clean_count=clean_count,
        )
        if status == STATUS_MICROWAVE_PRIMARY:
            logger.info(notes)
        elif status == STATUS_NO_HEALTHY_LINK:
            logger.error(notes)
        else:
            logger.warning(notes)

        return NetworkLinkReport(
            corridor_name=microwave_cfg.corridor_name,
            microwave_rtt_ms=round(micro_rtt_ms, 6),
            fiber_rtt_ms=round(fiber_rtt_ms, 6),
            latency_savings_ms=round(savings_ms, 6),
            latency_advantage_pct=round(advantage_pct, 2),
            selected_routing_link_id=selected_link_id,
            selected_link_type=selected_type,
            is_failover_active=status != STATUS_MICROWAVE_PRIMARY,
            status=status,
            audit_notes=notes,
            microwave_one_way_ms=round(micro_one_way_ms, 6),
            fiber_one_way_ms=round(fiber_one_way_ms, 6),
            microwave_propagation_one_way_ms=round(micro_prop_ms, 6),
            fiber_propagation_one_way_ms=round(fiber_prop_ms, 6),
            microwave_equipment_one_way_ms=round(micro_equip_ms, 6),
            fiber_equipment_one_way_ms=round(fiber_equip_ms, 6),
            is_microwave_lower_bound_only=not microwave_cfg.has_equipment_model,
            is_fiber_lower_bound_only=not fiber_cfg.has_equipment_model,
            degradation_reasons=reasons,
            consecutive_clean_evaluations=clean_count,
            weather_sensitivity_note=self._weather_sensitivity_note(microwave_cfg),
        )

    def _build_notes(
        self,
        status: str,
        microwave_cfg: NetworkLinkConfig,
        fiber_cfg: NetworkLinkConfig,
        micro_rtt_ms: float,
        fiber_rtt_ms: float,
        savings_ms: float,
        advantage_pct: float,
        reasons: Sequence[str],
        clean_count: int,
    ) -> str:
        """Render the human-readable audit line for ``status``."""
        corridor = microwave_cfg.corridor_name
        reason_text = "; ".join(reasons) if reasons else "none"
        bound_warning = ""
        if not microwave_cfg.has_equipment_model:
            bound_warning = (
                " NOTE: microwave figure is a propagation-only LOWER BOUND -- "
                "repeater_count/per_repeater_latency_us/fiber_tail_km are unset."
            )

        if status == STATUS_MICROWAVE_PRIMARY:
            return (
                f"PRIMARY ROUTE [{corridor}]: Microwave active ({microwave_cfg.link_id}). "
                f"RTT = {micro_rtt_ms:.3f}ms vs Fiber RTT = {fiber_rtt_ms:.3f}ms "
                f"(advantage = {savings_ms:.3f}ms / {advantage_pct:.1f}%)."
                f"{bound_warning}"
            )
        if status == STATUS_FAILOVER_RAIN_FADE:
            return (
                f"LINK FAILOVER [{corridor}]: Microwave '{microwave_cfg.link_id}' degraded "
                f"({reason_text}). Routed to backup fiber '{fiber_cfg.link_id}' "
                f"(RTT = {fiber_rtt_ms:.3f}ms, giving up {savings_ms:.3f}ms)."
            )
        if status == STATUS_FAILOVER_TELEMETRY_UNUSABLE:
            return (
                f"LINK FAILOVER [{corridor}]: Microwave '{microwave_cfg.link_id}' telemetry "
                f"unusable ({reason_text}). Failing closed to fiber '{fiber_cfg.link_id}' "
                f"(RTT = {fiber_rtt_ms:.3f}ms). An untrusted link state is not a healthy one."
            )
        if status == STATUS_HOLD_FIBER_HYSTERESIS:
            return (
                f"HOLD FIBER [{corridor}]: Microwave '{microwave_cfg.link_id}' is within "
                f"recovery thresholds but has only {clean_count}/"
                f"{self.recovery_dwell_evaluations} consecutive clean evaluations. Holding "
                f"fiber '{fiber_cfg.link_id}' to avoid route flap."
            )
        return (
            f"NO HEALTHY LINK [{corridor}]: both microwave '{microwave_cfg.link_id}' and fiber "
            f"'{fiber_cfg.link_id}' are degraded ({reason_text}). No route nominated -- escalate "
            f"and stop the cross-market strategy rather than routing over a degraded path."
        )
