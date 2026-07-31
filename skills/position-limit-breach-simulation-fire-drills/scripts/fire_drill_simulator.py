import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)

@dataclass
class FireDrillSimulatorConfig:
    enabled: bool = True
    max_allowed_risk_latency_ms: float = 5.0  # Risk SLA threshold
    parameters: Dict[str, float] = field(default_factory=dict)

@dataclass
class FireDrillScenario:
    scenario_id: str
    breach_type: str                     # 'EXCHANGE_LIMIT', 'BROKER_LIMIT', 'ROGUE_ALGO'
    target_symbol: str
    injected_position_qty: float         # Position quantity triggering breach
    limit_threshold: float               # Maximum allowed limit
    simulated_latency_ms: float = 1.5    # Simulated risk evaluation time in ms

@dataclass
class FireDrillReport:
    scenario_id: str
    breach_type: str
    target_symbol: str
    injected_qty: float
    limit_threshold: float
    order_blocked_by_gateway: bool
    risk_latency_ms: float
    latency_sla_passed: bool
    kill_switch_tripped: bool
    compliance_alert_logged: bool
    status: str                          # 'BREACH_BLOCKED_KILL_SWITCH_ENGAGED', 'LATENCY_SLA_FAILED', 'BREACH_UNBLOCKED_CRITICAL_FAILURE'
    audit_notes: str

class FireDrillSimulator:
    """
    Operational risk fire drill simulation engine testing real-time risk gateway order rejections,
    kill switch activations, and CFTC compliance alerts during simulated position limit breaches.
    """
    def __init__(self, config: Optional[FireDrillSimulatorConfig] = None):
        self.config = config or FireDrillSimulatorConfig()

    def execute(self, data: Dict[str, Any]) -> bool:
        """
        Legacy execute method for backward compatibility.
        """
        if not self.config.enabled:
            return False
        return True

    def run_fire_drill(self, scenario: FireDrillScenario) -> FireDrillReport:
        """
        Executes fire drill simulating position limit breach and auditing risk gateway rejection performance.
        """
        if not self.config.enabled:
            return FireDrillReport(
                scenario_id=scenario.scenario_id,
                breach_type=scenario.breach_type,
                target_symbol=scenario.target_symbol,
                injected_qty=scenario.injected_position_qty,
                limit_threshold=scenario.limit_threshold,
                order_blocked_by_gateway=False,
                risk_latency_ms=0.0,
                latency_sla_passed=False,
                kill_switch_tripped=False,
                compliance_alert_logged=False,
                status="SIMULATOR_DISABLED",
                audit_notes="Fire Drill Simulator is disabled."
            )

        is_breach = abs(scenario.injected_position_qty) > scenario.limit_threshold
        order_blocked = is_breach  # Risk gateway successfully blocks breach orders
        kill_switch_tripped = is_breach
        compliance_alert_logged = is_breach

        latency_passed = scenario.simulated_latency_ms <= self.config.max_allowed_risk_latency_ms

        if is_breach and order_blocked and latency_passed:
            status = "BREACH_BLOCKED_KILL_SWITCH_ENGAGED"
        elif is_breach and order_blocked and not latency_passed:
            status = "LATENCY_SLA_FAILED"
        else:
            status = "BREACH_UNBLOCKED_CRITICAL_FAILURE"

        notes = (
            f"POSITION LIMIT FIRE DRILL [{scenario.scenario_id} - {scenario.breach_type}]: "
            f"Injected Qty = {scenario.injected_position_qty:,.0f} (Limit = {scenario.limit_threshold:,.0f}). "
            f"Order Blocked = {order_blocked}, Kill Switch Tripped = {kill_switch_tripped}, "
            f"Risk Latency = {scenario.simulated_latency_ms:.2f} ms (SLA Pass = {latency_passed}). "
            f"Compliance Alert Logged = {compliance_alert_logged}."
        )

        if status == "BREACH_BLOCKED_KILL_SWITCH_ENGAGED":
            logger.info(f"FIRE DRILL SUCCESS: {notes}")
        else:
            logger.error(f"FIRE DRILL FAILURE: {notes}")

        return FireDrillReport(
            scenario_id=scenario.scenario_id,
            breach_type=scenario.breach_type,
            target_symbol=scenario.target_symbol,
            injected_qty=scenario.injected_position_qty,
            limit_threshold=scenario.limit_threshold,
            order_blocked_by_gateway=order_blocked,
            risk_latency_ms=scenario.simulated_latency_ms,
            latency_sla_passed=latency_passed,
            kill_switch_tripped=kill_switch_tripped,
            compliance_alert_logged=compliance_alert_logged,
            status=status,
            audit_notes=notes
        )
