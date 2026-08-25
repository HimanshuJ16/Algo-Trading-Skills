"""
fpga-based-market-data-processing-evaluation: capital-decision engine for
comparing a software (kernel-bypass CPU) tick-to-trade path against an FPGA
SmartNIC path, and for testing whether the measured latency reduction is
economically worth the hardware and firmware spend.

The engine answers one question: *given two measured latency profiles and a
cost structure, is the FPGA build justified?* It deliberately does **not**
model, forecast, or discover alpha. ``estimated_annual_alpha_gain_usd`` is an
exogenous input the caller must justify; the engine only checks it for internal
consistency against the caller's own stated alpha decay half-life.

Two failure modes dominate this decision, and both are guarded here:

1. **Incomparable latency measurements.** Published FPGA latencies are quoted
   under mutually incompatible definitions. AMD publishes "<3 ns transceiver
   latency" for the Alveo UL3524 -- a component figure, not a tick-to-trade
   path. The Exegy/AMD STAC-T0 record of 13.9 ns is *actionable latency*, where
   the clock starts only when the triggering field (e.g. a price) enters the
   FPGA, not at the beginning of the UDP frame. A software tick-to-trade
   number, by contrast, is normally quoted wire-to-wire (last bit of the
   inbound frame to the first/last bit of the outbound order). Subtracting one
   from the other is not a latency saving; it is an arithmetic error that
   happens to recommend a six-figure purchase. Every ``LatencyProfile``
   therefore has to declare its ``measurement_basis``, and the engine refuses to
   emit a spend recommendation when the two sides do not share one.

2. **A stock subtracted from a flow.** Non-recurring capital (card, IP core
   licence, initial HDL engineering) cannot be subtracted from an *annual*
   alpha figure as though it recurred every year. Capex is amortised
   straight-line over ``evaluation_horizon_years`` before the annual comparison,
   and the horizon total and payback period are reported alongside.

Modelling assumptions (stated, not hidden):

- **Alpha decay is treated as exponential in latency** when the caller supplies
  ``alpha_half_life_ns``: the fraction of edge surviving a latency ``L`` is
  ``2 ** (-L / T_half)``, so moving from ``L_cpu`` to ``L_fpga`` multiplies
  retained edge by ``2 ** (dL / T_half)``. This follows from the definition of a
  half-life; that a given signal's edge *actually* decays exponentially is an
  assumption about the strategy, not a measured fact. It is used only as a
  consistency check on the caller's claimed alpha gain, never to compute one.
- **Straight-line amortisation** of capex over the evaluation horizon, with no
  discounting, no tax shield, and no residual value. For a decision at this
  size, run the resulting cash flows through a proper NPV model as well.

Limitations:

- ``daily_trade_frequency`` is recorded as context and is **not** a decision
  input; there is no defensible general mapping from trade count to alpha.
- The engine compares exactly two topologies. It does not model a partial
  offload (FPGA parse plus host decision) other than through whatever latency
  the caller measured -- see ``PCIE_DMA_ROUND_TRIP_FLOOR_NS`` and the pitfalls
  in SKILL.md.
- Percentile inputs are taken as given. ``sample_count`` is checked only for the
  arithmetic minimum needed for a p99 to exist at all.

References
----------
- STAC-T0 (tick-to-trade network I/O), STAC Benchmark Council:
  https://stacresearch.com/benchmarks/
- STAC Report, Exegy/AMD FPGA solution, 13.9 ns actionable latency:
  https://docs.stacresearch.com/news/AMD240422
- AMD Alveo UL3524 product brief ("Less than 3ns transceiver latency"):
  https://www.xilinx.com/content/dam/xilinx/publications/product-briefs/2233051_Product_Brief_UL3524_Alveo_Accelerator_Card.pdf
- Commission Delegated Regulation (EU) 2017/574 (RTS 25), Annex Table 2.
"""
import logging
import math
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)

#: Wire-to-wire: last bit of the inbound market-data frame on the wire to the
#: first bit of the outbound order frame on the wire, hardware-timestamped
#: outside the system under test. The only basis that captures the whole path.
BASIS_WIRE_TO_WIRE = "WIRE_TO_WIRE"

#: STAC-T0 "actionable latency": the clock starts when the triggering field
#: (e.g. a price) enters the device, not at the start of the inbound frame, so
#: inbound framing/deserialisation is excluded. Comparable only with another
#: STAC-T0 actionable number.
BASIS_STAC_T0_ACTIONABLE = "STAC_T0_ACTIONABLE"

#: In-process software timestamps only. Excludes NIC, driver and wire time on
#: both sides, and excludes proportionally *more* of the software path than of
#: the FPGA path.
BASIS_APPLICATION_INTERNAL = "APPLICATION_INTERNAL"

#: A vendor component figure (transceiver latency, IP-core latency, MAC
#: latency). Never a tick-to-trade number; the engine will not price against it.
BASIS_VENDOR_COMPONENT = "VENDOR_COMPONENT"

VALID_MEASUREMENT_BASES = frozenset({
    BASIS_WIRE_TO_WIRE,
    BASIS_STAC_T0_ACTIONABLE,
    BASIS_APPLICATION_INTERNAL,
    BASIS_VENDOR_COMPONENT,
})

RECOMMENDATION_FPGA = "FPGA_RECOMMENDED"
RECOMMENDATION_SOFTWARE = "SOFTWARE_CPU_SUFFICIENT"
#: Emitted when the inputs cannot support *any* verdict. Fail closed: never
#: recommend a capital purchase off an incomparable or self-contradictory input.
RECOMMENDATION_INSUFFICIENT = "INSUFFICIENT_EVIDENCE"

#: A p99 is undefined below 100 observations -- the 99th percentile of fewer
#: than 100 samples is just the maximum. This is the arithmetic floor, not a
#: sufficiency criterion; a *stable* p99 needs orders of magnitude more.
MIN_SAMPLES_FOR_P99 = 100

#: Best published FPGA-to-host PCIe DMA round-trip times: 585 ns (Orthogone ULL
#: DMA controller, Gen4 x8) and 790 ns (DMA Calypte, Gen3 x8). Any design that
#: parses in the FPGA but decides on the host pays this on every message, which
#: is one to two orders of magnitude above the FPGA's internal figure. Provided
#: for sanity-checking; not applied automatically, because the engine cannot
#: know the caller's topology.
PCIE_DMA_ROUND_TRIP_FLOOR_NS = 585.0

# Data-quality flag identifiers.
FLAG_BASIS_MISMATCH = "MEASUREMENT_BASIS_MISMATCH"
FLAG_COMPONENT_BASIS = "VENDOR_COMPONENT_BASIS_NOT_TICK_TO_TRADE"
FLAG_APPLICATION_BASIS = "APPLICATION_INTERNAL_BASIS_EXCLUDES_NIC_AND_WIRE"
FLAG_ZERO_TAIL_SPREAD = "FPGA_TAIL_SPREAD_ZERO"
FLAG_INSUFFICIENT_SAMPLES = "INSUFFICIENT_SAMPLES_FOR_P99"
FLAG_NEGATIVE_LATENCY_DELTA = "FPGA_SLOWER_THAN_SOFTWARE_AT_P50"
FLAG_ALPHA_DECAY_INCONSISTENT = "ALPHA_GAIN_INCONSISTENT_WITH_DECAY_HALF_LIFE"


def _require_finite(name: str, value: float) -> float:
    """Reject NaN/Inf before they propagate silently into a spend decision."""
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{name} must be a finite number, got {value!r}")
    return numeric


def _require_non_negative(name: str, value: float) -> float:
    numeric = _require_finite(name, value)
    if numeric < 0.0:
        raise ValueError(f"{name} must be >= 0, got {numeric}")
    return numeric


def _require_positive(name: str, value: float) -> float:
    numeric = _require_finite(name, value)
    if numeric <= 0.0:
        raise ValueError(f"{name} must be > 0, got {numeric}")
    return numeric


@dataclass
class LatencyProfile:
    """
    A measured tick-to-trade latency distribution for one topology.

    ``measurement_basis`` is required and has no default on purpose: a profile
    whose basis is unknown cannot be compared with anything, and defaulting it
    would silently reintroduce the exact error this engine exists to prevent.
    """
    p50_latency_ns: float
    p99_latency_ns: float
    max_latency_ns: float
    std_dev_jitter_ns: float
    measurement_basis: str
    #: Number of observations behind the percentiles, if known.
    sample_count: Optional[int] = None

    def __post_init__(self) -> None:
        self.p50_latency_ns = _require_non_negative("p50_latency_ns", self.p50_latency_ns)
        self.p99_latency_ns = _require_non_negative("p99_latency_ns", self.p99_latency_ns)
        self.max_latency_ns = _require_non_negative("max_latency_ns", self.max_latency_ns)
        self.std_dev_jitter_ns = _require_non_negative("std_dev_jitter_ns", self.std_dev_jitter_ns)

        if self.measurement_basis not in VALID_MEASUREMENT_BASES:
            raise ValueError(
                f"measurement_basis must be one of {sorted(VALID_MEASUREMENT_BASES)}, "
                f"got {self.measurement_basis!r}"
            )
        # A distribution must be monotonic in its own order statistics. Violating
        # this is a transcription error, and it silently inverts the jitter ratio.
        if not (self.p50_latency_ns <= self.p99_latency_ns <= self.max_latency_ns):
            raise ValueError(
                "latency percentiles must satisfy p50 <= p99 <= max, got "
                f"p50={self.p50_latency_ns}, p99={self.p99_latency_ns}, max={self.max_latency_ns}"
            )
        if self.sample_count is not None:
            if int(self.sample_count) != self.sample_count or self.sample_count <= 0:
                raise ValueError(f"sample_count must be a positive integer, got {self.sample_count!r}")
            self.sample_count = int(self.sample_count)


@dataclass
class StrategyAlphaMetrics:
    """
    Caller-supplied economics for the strategy that would run on the accelerated path.

    ``estimated_annual_alpha_gain_usd`` is the *incremental* annual profit the
    caller attributes to the latency reduction. The engine does not derive it.

    ``alpha_half_life_ns`` is the latency at which the strategy's edge is halved.
    Supply ``None`` when it is unknown; the consistency check is then skipped
    rather than assumed away.
    """
    daily_trade_frequency: int
    alpha_half_life_ns: Optional[float]
    estimated_annual_alpha_gain_usd: float

    def __post_init__(self) -> None:
        if int(self.daily_trade_frequency) != self.daily_trade_frequency or self.daily_trade_frequency < 0:
            raise ValueError(
                f"daily_trade_frequency must be a non-negative integer, got {self.daily_trade_frequency!r}"
            )
        self.daily_trade_frequency = int(self.daily_trade_frequency)
        if self.alpha_half_life_ns is not None:
            self.alpha_half_life_ns = _require_positive("alpha_half_life_ns", self.alpha_half_life_ns)
        self.estimated_annual_alpha_gain_usd = _require_finite(
            "estimated_annual_alpha_gain_usd", self.estimated_annual_alpha_gain_usd
        )


@dataclass
class FpgaHardwareCosts:
    """
    Cost structure, split by recurrence because the two cannot be added together.

    One-time: ``smartnic_hardware_usd``, ``ip_core_licensing_usd`` (perpetual
    licence), ``one_time_hdl_engineering_usd`` (initial NRE / integration).
    Recurring: ``annual_engineering_maintenance_usd``,
    ``annual_ip_core_subscription_usd`` (where the vendor bills annually rather
    than perpetually -- put the licence in exactly one of the two fields).
    """
    smartnic_hardware_usd: float
    ip_core_licensing_usd: float
    annual_engineering_maintenance_usd: float
    annual_ip_core_subscription_usd: float = 0.0
    one_time_hdl_engineering_usd: float = 0.0

    def __post_init__(self) -> None:
        self.smartnic_hardware_usd = _require_non_negative("smartnic_hardware_usd", self.smartnic_hardware_usd)
        self.ip_core_licensing_usd = _require_non_negative("ip_core_licensing_usd", self.ip_core_licensing_usd)
        self.annual_engineering_maintenance_usd = _require_non_negative(
            "annual_engineering_maintenance_usd", self.annual_engineering_maintenance_usd
        )
        self.annual_ip_core_subscription_usd = _require_non_negative(
            "annual_ip_core_subscription_usd", self.annual_ip_core_subscription_usd
        )
        self.one_time_hdl_engineering_usd = _require_non_negative(
            "one_time_hdl_engineering_usd", self.one_time_hdl_engineering_usd
        )

    @property
    def one_time_capital_usd(self) -> float:
        """Non-recurring spend: card, perpetual IP licence, initial HDL NRE."""
        return self.smartnic_hardware_usd + self.ip_core_licensing_usd + self.one_time_hdl_engineering_usd

    @property
    def recurring_annual_usd(self) -> float:
        """Spend that repeats every year of the evaluation horizon."""
        return self.annual_engineering_maintenance_usd + self.annual_ip_core_subscription_usd


@dataclass
class FpgaEvaluationReport:
    software_cpu_profile: LatencyProfile
    fpga_hardware_profile: LatencyProfile
    #: The shared basis when both profiles agree, otherwise ``"<cpu>/<fpga>"``
    #: so a mismatched comparison cannot be filed away under one of them.
    measurement_basis: str
    median_latency_reduction_ns: float
    worst_case_latency_reduction_ns: float
    #: (cpu p99 - cpu p50) / (fpga p99 - fpga p50). ``math.inf`` when the FPGA
    #: tail spread is exactly zero -- see ``FLAG_ZERO_TAIL_SPREAD``.
    tail_jitter_reduction_ratio: float
    evaluation_horizon_years: float
    #: Total cash cost over the evaluation horizon: capex + recurring * years.
    total_cost_of_ownership_usd: float
    #: Capex amortised straight-line over the horizon, plus recurring cost. This
    #: is the figure the annual alpha gain is compared against.
    annualized_cost_of_ownership_usd: float
    projected_annual_alpha_gain_usd: float
    net_annual_roi_usd: float
    net_horizon_roi_usd: float
    #: Years for the annual surplus over recurring cost to repay capex.
    #: ``None`` when the surplus is not positive (never pays back).
    payback_period_years: Optional[float]
    #: 2 ** (dL / half_life). ``None`` when no half-life was supplied.
    alpha_capture_uplift_factor: Optional[float]
    recommendation: str
    data_quality_flags: List[str] = field(default_factory=list)
    audit_notes: str = ""


class FpgaMarketDataEvaluationEngine:
    """
    Decides whether an FPGA SmartNIC tick-to-trade build is justified by a
    measured latency reduction and the caller's cost and alpha inputs.

    Both gates must pass for ``FPGA_RECOMMENDED``:

    - median (p50) latency reduction ``>= min_latency_reduction_threshold_ns``;
    - annual alpha gain exceeds the annualised cost of ownership by strictly
      more than ``min_roi_net_benefit_usd``.

    Both thresholds are **house policy, not industry standards**. The defaults
    (1 microsecond, zero margin) encode "only bother if you save at least a
    microsecond, and only if it pays for itself"; set them to whatever your
    investment committee actually requires.
    """

    def __init__(
        self,
        min_latency_reduction_threshold_ns: float = 1000.0,
        min_roi_net_benefit_usd: float = 0.0,
        evaluation_horizon_years: float = 3.0,
        min_alpha_uplift_for_material_gain: float = 1.01,
        enforce_alpha_decay_consistency: bool = True,
    ) -> None:
        """
        Args:
            min_latency_reduction_threshold_ns: Minimum p50 reduction to consider
                the build. House policy; there is no standard value.
            min_roi_net_benefit_usd: Margin of safety the annual net benefit must
                exceed. Default 0.0 matches the documented rule "annual alpha gain
                must exceed annualised TCO"; raise it for a real margin.
            evaluation_horizon_years: Amortisation window for one-time capital,
                typically the card's expected service life before refresh.
            min_alpha_uplift_for_material_gain: Below this edge-retention uplift
                factor the claimed alpha gain is treated as inconsistent with the
                caller's own alpha half-life. 1.01 = a 1% relative improvement.
            enforce_alpha_decay_consistency: When False, the half-life check still
                populates ``alpha_capture_uplift_factor`` and raises a flag but
                does not downgrade the recommendation.
        """
        self.min_latency_reduction_threshold_ns = _require_non_negative(
            "min_latency_reduction_threshold_ns", min_latency_reduction_threshold_ns
        )
        self.min_roi_net_benefit_usd = _require_finite("min_roi_net_benefit_usd", min_roi_net_benefit_usd)
        self.evaluation_horizon_years = _require_positive("evaluation_horizon_years", evaluation_horizon_years)
        self.min_alpha_uplift_for_material_gain = _require_positive(
            "min_alpha_uplift_for_material_gain", min_alpha_uplift_for_material_gain
        )
        self.enforce_alpha_decay_consistency = bool(enforce_alpha_decay_consistency)

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _audit_measurement_bases(
        cpu_profile: LatencyProfile, fpga_profile: LatencyProfile
    ) -> List[str]:
        """Flags describing how comparable the two profiles are."""
        flags: List[str] = []
        if BASIS_VENDOR_COMPONENT in (cpu_profile.measurement_basis, fpga_profile.measurement_basis):
            flags.append(FLAG_COMPONENT_BASIS)
        if cpu_profile.measurement_basis != fpga_profile.measurement_basis:
            flags.append(FLAG_BASIS_MISMATCH)
        elif cpu_profile.measurement_basis == BASIS_APPLICATION_INTERNAL:
            flags.append(FLAG_APPLICATION_BASIS)
        return flags

    @staticmethod
    def _tail_jitter_ratio(cpu_profile: LatencyProfile, fpga_profile: LatencyProfile) -> float:
        """
        Ratio of tail spreads (p99 - p50). Both spreads are non-negative by
        construction (``LatencyProfile`` enforces p50 <= p99 <= max), so the only
        degenerate case is an FPGA spread of exactly zero, reported as infinite
        rather than absorbed by an arbitrary denominator floor.
        """
        cpu_spread = cpu_profile.p99_latency_ns - cpu_profile.p50_latency_ns
        fpga_spread = fpga_profile.p99_latency_ns - fpga_profile.p50_latency_ns
        if fpga_spread == 0.0:
            return math.inf if cpu_spread > 0.0 else 1.0
        return round(cpu_spread / fpga_spread, 4)

    @staticmethod
    def _alpha_capture_uplift(delta_p50_ns: float, half_life_ns: Optional[float]) -> Optional[float]:
        """
        Relative edge-retention uplift ``2 ** (dL / T_half)`` under an assumed
        exponential decay of edge in latency. ``None`` when no half-life is
        supplied; ``math.inf`` if the exponent overflows a float.
        """
        if half_life_ns is None:
            return None
        try:
            return math.pow(2.0, delta_p50_ns / half_life_ns)
        except OverflowError:
            return math.inf

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def evaluate_fpga_acceleration(
        self,
        cpu_profile: LatencyProfile,
        fpga_profile: LatencyProfile,
        strategy_metrics: StrategyAlphaMetrics,
        costs: FpgaHardwareCosts,
    ) -> FpgaEvaluationReport:
        """
        Audit a software vs FPGA tick-to-trade comparison and return a
        recommendation.

        Returns ``INSUFFICIENT_EVIDENCE`` -- never a spend recommendation --
        when the two latency profiles are not measured on the same basis, when
        either is a vendor component figure, or when the claimed alpha gain
        contradicts the caller's own alpha half-life.

        Raises:
            TypeError: if an argument is not of the expected dataclass type.
        """
        for name, value, expected in (
            ("cpu_profile", cpu_profile, LatencyProfile),
            ("fpga_profile", fpga_profile, LatencyProfile),
            ("strategy_metrics", strategy_metrics, StrategyAlphaMetrics),
            ("costs", costs, FpgaHardwareCosts),
        ):
            if not isinstance(value, expected):
                raise TypeError(f"{name} must be a {expected.__name__}, got {type(value).__name__}")

        flags = self._audit_measurement_bases(cpu_profile, fpga_profile)
        reported_basis = (
            cpu_profile.measurement_basis
            if cpu_profile.measurement_basis == fpga_profile.measurement_basis
            else f"{cpu_profile.measurement_basis}/{fpga_profile.measurement_basis}"
        )

        # 1. Latency and jitter metrics.
        delta_p50_ns = round(cpu_profile.p50_latency_ns - fpga_profile.p50_latency_ns, 4)
        delta_max_ns = round(cpu_profile.max_latency_ns - fpga_profile.max_latency_ns, 4)
        jitter_ratio = self._tail_jitter_ratio(cpu_profile, fpga_profile)

        if math.isinf(jitter_ratio):
            flags.append(FLAG_ZERO_TAIL_SPREAD)
        if delta_p50_ns < 0.0:
            flags.append(FLAG_NEGATIVE_LATENCY_DELTA)
        if any(
            profile.sample_count is not None and profile.sample_count < MIN_SAMPLES_FOR_P99
            for profile in (cpu_profile, fpga_profile)
        ):
            flags.append(FLAG_INSUFFICIENT_SAMPLES)

        # 2. Cost of ownership. Capex is amortised before meeting an annual flow.
        horizon = self.evaluation_horizon_years
        capex = costs.one_time_capital_usd
        recurring = costs.recurring_annual_usd
        tco_usd = round(capex + recurring * horizon, 2)
        annualized_cost_usd = round(capex / horizon + recurring, 2)

        annual_gain = strategy_metrics.estimated_annual_alpha_gain_usd
        net_annual_roi_usd = round(annual_gain - annualized_cost_usd, 2)
        net_horizon_roi_usd = round(annual_gain * horizon - tco_usd, 2)

        annual_surplus = annual_gain - recurring
        payback_years = round(capex / annual_surplus, 4) if annual_surplus > 0.0 else None

        # 3. Consistency of the claimed alpha gain with the stated decay half-life.
        uplift = self._alpha_capture_uplift(delta_p50_ns, strategy_metrics.alpha_half_life_ns)
        alpha_inconsistent = (
            uplift is not None
            and uplift < self.min_alpha_uplift_for_material_gain
            and annual_gain > 0.0
        )
        if alpha_inconsistent:
            flags.append(FLAG_ALPHA_DECAY_INCONSISTENT)

        # 4. Decision.
        blocking_flags = [f for f in flags if f in (FLAG_COMPONENT_BASIS, FLAG_BASIS_MISMATCH)]
        is_latency_justified = delta_p50_ns >= self.min_latency_reduction_threshold_ns
        is_financial_justified = net_annual_roi_usd > self.min_roi_net_benefit_usd
        alpha_blocks = alpha_inconsistent and self.enforce_alpha_decay_consistency

        if blocking_flags:
            rec = RECOMMENDATION_INSUFFICIENT
            notes = (
                "INSUFFICIENT EVIDENCE: the two latency profiles are not comparable "
                f"(cpu basis={cpu_profile.measurement_basis}, fpga basis={fpga_profile.measurement_basis}; "
                f"flags={', '.join(blocking_flags)}). A vendor component or actionable-latency figure is not a "
                "wire-to-wire tick-to-trade measurement; re-benchmark both paths on one basis before pricing "
                f"the build. The {delta_p50_ns:,.1f}ns delta is arithmetic only, not a latency saving."
            )
            logger.warning(notes)
        elif is_latency_justified and is_financial_justified and alpha_blocks:
            rec = RECOMMENDATION_INSUFFICIENT
            notes = (
                f"INSUFFICIENT EVIDENCE: claimed annual alpha gain of ${annual_gain:,.2f} is inconsistent with the "
                f"supplied alpha half-life of {strategy_metrics.alpha_half_life_ns:,.0f}ns. Under the exponential "
                f"decay assumption a {delta_p50_ns:,.1f}ns reduction improves edge retention by a factor of "
                f"{uplift:.6f}, below the {self.min_alpha_uplift_for_material_gain:.4f} materiality threshold. "
                "Reconcile the alpha estimate with the decay assumption before committing capital."
            )
            logger.warning(notes)
        elif is_latency_justified and is_financial_justified:
            rec = RECOMMENDATION_FPGA
            jitter_text = (
                "infinite (zero measured FPGA tail spread)" if math.isinf(jitter_ratio) else f"{jitter_ratio}x"
            )
            payback_text = (
                f", payback in {payback_years:,.2f} years." if payback_years is not None else ", no payback."
            )
            notes = (
                f"FPGA ACCELERATION RECOMMENDED (basis={cpu_profile.measurement_basis}): cuts median tick-to-trade "
                f"latency by {delta_p50_ns:,.1f}ns ({cpu_profile.p50_latency_ns:,.1f}ns -> "
                f"{fpga_profile.p50_latency_ns:,.1f}ns) with {jitter_text} tail-spread reduction and "
                f"{delta_max_ns:,.1f}ns worst-case reduction. Net annual ROI = ${net_annual_roi_usd:,.2f} "
                f"(gain ${annual_gain:,.2f} vs annualised cost ${annualized_cost_usd:,.2f}); "
                f"{horizon:g}-year TCO ${tco_usd:,.2f}, net horizon ROI ${net_horizon_roi_usd:,.2f}"
                f"{payback_text}"
            )
            logger.info(notes)
        else:
            rec = RECOMMENDATION_SOFTWARE
            reasons = []
            if not is_latency_justified:
                reasons.append(
                    f"p50 latency saving {delta_p50_ns:,.1f}ns < required "
                    f"{self.min_latency_reduction_threshold_ns:,.1f}ns"
                )
            if not is_financial_justified:
                reasons.append(
                    f"net annual ROI ${net_annual_roi_usd:,.2f} <= required margin "
                    f"${self.min_roi_net_benefit_usd:,.2f} (annualised cost ${annualized_cost_usd:,.2f})"
                )
            notes = (
                f"SOFTWARE CPU SUFFICIENT: FPGA build not justified ({'; '.join(reasons)}). "
                "Recommend continued DPDK/EF_VI kernel-bypass optimisation."
            )
            logger.warning(notes)

        return FpgaEvaluationReport(
            software_cpu_profile=cpu_profile,
            fpga_hardware_profile=fpga_profile,
            measurement_basis=reported_basis,
            median_latency_reduction_ns=delta_p50_ns,
            worst_case_latency_reduction_ns=delta_max_ns,
            tail_jitter_reduction_ratio=jitter_ratio,
            evaluation_horizon_years=horizon,
            total_cost_of_ownership_usd=tco_usd,
            annualized_cost_of_ownership_usd=annualized_cost_usd,
            projected_annual_alpha_gain_usd=annual_gain,
            net_annual_roi_usd=net_annual_roi_usd,
            net_horizon_roi_usd=net_horizon_roi_usd,
            payback_period_years=payback_years,
            alpha_capture_uplift_factor=uplift,
            recommendation=rec,
            data_quality_flags=flags,
            audit_notes=notes,
        )
