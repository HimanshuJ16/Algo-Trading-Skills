import logging
import math
from dataclasses import dataclass
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)


def _require_finite(value: float, label: str, minimum: float = None) -> None:
    """Rejects non-finite values — a NaN would make every threshold
    comparison False and silently approve the routing decision."""
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number, got {value!r}")
    if minimum is not None and value < minimum:
        raise ValueError(f"{label} must be >= {minimum}, got {value!r}")


@dataclass
class BrokerProfile:
    broker_id: str
    name: str
    max_nav_pct_limit: float          # e.g. 0.35 (35% NAV cap)
    cds_spread_bps: float             # Credit Default Swap spread in bps (e.g. 85.0)
    max_cds_bps_threshold: float      # Max allowed CDS before blocking (e.g. 250.0)
    current_cash: float
    current_margin: float
    current_positions_value: float    # may be negative (short market value)

    def __post_init__(self) -> None:
        if not isinstance(self.broker_id, str) or not self.broker_id:
            raise ValueError("broker_id must be a non-empty string")
        if not isinstance(self.name, str) or not self.name:
            raise ValueError(f"name must be a non-empty string (broker {self.broker_id})")
        _require_finite(self.max_nav_pct_limit, f"max_nav_pct_limit ({self.broker_id})", minimum=0.0)
        if self.max_nav_pct_limit > 1.0:
            raise ValueError(
                f"max_nav_pct_limit for {self.broker_id} must be a fraction in (0, 1], "
                f"got {self.max_nav_pct_limit}"
            )
        _require_finite(self.cds_spread_bps, f"cds_spread_bps ({self.broker_id})", minimum=0.0)
        _require_finite(
            self.max_cds_bps_threshold, f"max_cds_bps_threshold ({self.broker_id})", minimum=0.0
        )
        # Balances are signed (negative cash = debit balance, negative
        # positions = short market value) but must be finite.
        for label, v in (
            ("current_cash", self.current_cash),
            ("current_margin", self.current_margin),
            ("current_positions_value", self.current_positions_value),
        ):
            _require_finite(v, f"{label} ({self.broker_id})")


@dataclass
class RoutingDecision:
    selected_broker_id: str
    is_rerouted: bool
    original_broker_id: str
    projected_nav_pct: float
    reason: str
    blocked: bool = False             # True => route NOWHERE and escalate;
                                      # selected_broker_id still names the
                                      # original target for audit context only


class CounterpartyConcentrationMonitor:
    """
    Monitors prime broker counterparty concentration exposures, credit default
    signals, enforces % NAV caps, and provides smart failover order routing.

    Routing model: a proposed order of value V adds V to the target broker's
    exposure while portfolio NAV is held constant — i.e. it is treated as
    margin-financed or externally sourced value (the conservative convention:
    the broker's claim grows, the fund's assets do not). Decisions are
    advisory: route_order never mutates broker state; the caller executes the
    selected route. A decision with blocked=True must NOT be routed anywhere.

    Concentration limits, CDS thresholds, and the HHI alert level are
    engineering defaults, not regulatory prescriptions — calibrate them to
    the fund's counterparty risk policy and PB agreements.
    """

    def __init__(
        self,
        brokers: List[BrokerProfile] = None,
        hhi_alert_threshold: float = 0.35,
    ):
        _require_finite(hhi_alert_threshold, "hhi_alert_threshold", minimum=0.0)
        if hhi_alert_threshold > 1.0:
            raise ValueError(f"hhi_alert_threshold must be in [0, 1], got {hhi_alert_threshold}")
        self.brokers: Dict[str, BrokerProfile] = {}
        self.hhi_alert_threshold = hhi_alert_threshold
        for b in (brokers or []):
            self.register_broker(b)

    def register_broker(self, broker: BrokerProfile):
        if broker.broker_id in self.brokers:
            logger.info(
                "Re-registering broker %s - existing profile replaced (update semantics).",
                broker.broker_id,
            )
        self.brokers[broker.broker_id] = broker

    def calculate_total_broker_exposure(self, broker_id: str) -> float:
        if broker_id not in self.brokers:
            # A silent 0.0 for an unknown id would understate exposure to a
            # real counterparty after a typo — refuse instead.
            raise ValueError(
                f"Unknown broker {broker_id!r}; registered: {sorted(self.brokers)}"
            )
        b = self.brokers[broker_id]
        return b.current_cash + b.current_margin + b.current_positions_value

    def calculate_portfolio_nav(self) -> float:
        return sum(self.calculate_total_broker_exposure(bid) for bid in self.brokers)

    def compute_hhi(self) -> float:
        """Herfindahl-Hirschman Index over broker exposure weights
        (1/n for perfectly equal exposure, 1.0 for a single broker).
        Logs a warning when the index exceeds hhi_alert_threshold."""
        total_nav = self.calculate_portfolio_nav()
        if total_nav <= 0:
            logger.warning(
                "Portfolio NAV is %s - HHI undefined; returning 0.0.", total_nav
            )
            return 0.0
        weights = [self.calculate_total_broker_exposure(bid) / total_nav for bid in self.brokers]
        hhi = round(float(sum(w ** 2 for w in weights)), 4)
        if hhi > self.hhi_alert_threshold:
            logger.warning(
                "Broker concentration HHI %.4f exceeds alert threshold %.2f "
                "- review counterparty diversification.", hhi, self.hhi_alert_threshold,
            )
        return hhi

    def route_order(self, target_broker_id: str, proposed_order_value: float) -> RoutingDecision:
        """
        Evaluates proposed order routing to target_broker_id against
        concentration caps and CDS thresholds. Re-routes to the compliant
        secondary broker with the lowest projected NAV weight; if none is
        compliant, returns a blocked=True decision — route nowhere and
        escalate.
        """
        _require_finite(proposed_order_value, "proposed_order_value", minimum=0.0)
        if target_broker_id not in self.brokers:
            raise ValueError(f"Unknown target broker {target_broker_id}")

        total_nav = self.calculate_portfolio_nav()
        if total_nav <= 0:
            # No assessable NAV (empty or net-negative book): concentration
            # cannot be measured, so fail closed rather than inventing a
            # denominator.
            reason = (
                f"Portfolio NAV is {total_nav} - concentration cannot be assessed; "
                "execution blocked pending manual review."
            )
            logger.critical(reason)
            return RoutingDecision(
                selected_broker_id=target_broker_id,
                is_rerouted=False,
                original_broker_id=target_broker_id,
                projected_nav_pct=0.0,
                reason=reason,
                blocked=True,
            )

        primary = self.brokers[target_broker_id]
        primary_current_exp = self.calculate_total_broker_exposure(target_broker_id)
        primary_proj_weight = (primary_current_exp + proposed_order_value) / total_nav

        # Check Primary Broker Compliance
        is_cds_distressed = primary.cds_spread_bps > primary.max_cds_bps_threshold
        is_nav_breached = primary_proj_weight > primary.max_nav_pct_limit

        if not is_cds_distressed and not is_nav_breached:
            return RoutingDecision(
                selected_broker_id=target_broker_id,
                is_rerouted=False,
                original_broker_id=target_broker_id,
                projected_nav_pct=round(primary_proj_weight * 100, 2),
                reason="Order approved for primary broker within exposure limits.",
            )

        # Primary Broker Breached! Search Secondary Brokers for Failover Routing
        logger.warning(
            f"Primary Broker {target_broker_id} BREACHED (NAV Weight: "
            f"{primary_proj_weight*100:.1f}%, CDS: {primary.cds_spread_bps}bps). "
            f"Searching failover..."
        )

        candidate_brokers: List[Tuple[str, float]] = []
        for bid, b in self.brokers.items():
            if bid == target_broker_id:
                continue
            if b.cds_spread_bps > b.max_cds_bps_threshold:
                continue

            curr_exp = self.calculate_total_broker_exposure(bid)
            proj_weight = (curr_exp + proposed_order_value) / total_nav
            if proj_weight <= b.max_nav_pct_limit:
                candidate_brokers.append((bid, proj_weight))

        if candidate_brokers:
            # Select broker with lowest projected weight
            candidate_brokers.sort(key=lambda x: (x[1], x[0]))
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
                reason=(
                    "All prime brokers exceed concentration limits or CDS "
                    "thresholds. Execution blocked - route nowhere and escalate "
                    "to manual review."
                ),
                blocked=True,
            )
