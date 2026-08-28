"""
position-limit-breach-simulation-fire-drills: harness for scheduled position-limit
breach fire drills against a pre-trade risk gateway and post-trade exposure controls.

The harness does **not** simulate the risk control. It records what the system under
test actually did (``ObservedControlResponse``) and compares that against the outcome
the scenario says is correct (``ExpectedOutcome``). A drill can therefore fail, which
is the only reason to run one. A harness that derives "the gateway blocked it" from
"the position exceeded the limit" reports success unconditionally and cannot detect an
open gateway.

Three scenario outcomes are modelled, because position-limit breaches arrive in three
different shapes:

- ``BLOCK_AND_HALT``  - an order would take the account over the limit. The pre-trade
  control must reject it and disable the strategy.
- ``ALERT_ONLY``      - the account is over the limit with no order to reject (option
  assignment, a delta re-evaluation at the close, a scheduled spot-month limit
  step-down, or exposure aggregated across venues). Only a post-trade control can see
  this; it must alert and drive a remediation action.
- ``ALLOW``           - a negative control. The position is within the limit, or it is
  over the limit under a documented exemption or grace period, and the gateway must
  **not** block it. A suite with no negative control cannot detect a gateway that
  blocks everything.

Sourced facts this module encodes
---------------------------------
- Intraday positions count. CME/CBOT/NYMEX/COMEX Rule 562 ("Position Limit
  Violations"), as reproduced in CME Group Market Regulation Advisory Notice RA2601-5
  (effective 12 March 2026): "Any positions, including positions established intraday,
  in excess of those permitted under the rules of the Exchange shall be deemed position
  limit violations." An end-of-day-only drill therefore tests the wrong thing.
- "In excess of", not "at". A position exactly equal to the limit is compliant, so the
  breach predicate is a strict inequality (see ``is_over_limit``).
- Grace and exemption paths exist and are not gateway failures. Rule 562 allows one
  business day to liquidate an overage caused by an option assignment, and does not
  treat a position as violating where it exceeds limits on the current close's delta
  factors but not on the previous day's. Rule 559 allows a bona fide hedging
  application filed within five business days of assuming the position. These are the
  ``ALLOW`` scenarios, and each one requires a recorded ``exemption_basis``.
- Position limits for derivatives are also a *post-trade* control in the EU/UK regime.
  RTS 6 (Commission Delegated Regulation (EU) 2017/589) Art. 17(4): "For derivatives,
  the post-trade controls referred to in paragraph 1 shall include controls regarding
  the maximum long and short and overall strategy positions". Art. 15(5) separately
  requires blocking orders that "risk compromising the investment firm's own risk
  thresholds". Both halves need drilling.
- A tripped control must stay tripped. RTS 6 Art. 15(3): after a pre-determined number
  of repeated executions "the trading system shall be automatically disabled until
  re-enabled by a designated staff member". A kill switch that resumes on its own is a
  finding, so ``manual_reenable_required`` is asserted whenever trading is halted.
- Drills must not touch production. RTS 6 Art. 10 (Stress testing): "The investment
  firm shall ensure that the tests are carried out in such a way that they do not
  affect the production environment." ``PRODUCTION`` scenarios raise
  ``ProductionEnvironmentError`` rather than returning a failed report.
- Alert timing has a published number; gateway rejection does not. RTS 6 Art. 16(5):
  "Real-time alerts shall be generated within five seconds after the relevant event."
  That is the basis for ``max_alert_latency_ms = 5000.0``. No regulator publishes a
  maximum pre-trade risk-check latency - SEC Rule 15c3-5 is a "reasonably designed"
  standard - so ``max_pre_trade_latency_ms = 5.0`` is an internal SLA default that must
  be calibrated per firm, not a compliance threshold.

Limitations (deliberate, documented)
------------------------------------
- **Not a risk gateway.** Nothing here rejects an order or trips a kill switch. It
  grades evidence produced by the real control, so its verdict is only as good as the
  observation the caller supplies. Wiring it to a mock that mirrors the scenario
  reproduces the tautology it exists to remove.
- **Limits are scalar and symmetric.** ``limit_threshold`` is compared against
  ``abs(injected_position_qty)``. Where an exchange sets different net-long and
  net-short limits, or spot-month and all-months-combined limits, run one scenario per
  limit rather than folding them together.
- **Futures-equivalent conversion is out of scope.** Options delta weighting, month
  aggregation and referenced-contract equivalence (17 CFR 150.2) must be applied by the
  caller before the quantity reaches a scenario.
- **Jurisdiction is the caller's to establish.** The rule references above are US
  designated-contract-market (CME Group) and EU/UK (RTS 6). CFTC Part 150 federal
  limits, SEC Rule 15c3-5 (US broker-dealers with market access, not futures), and
  MiFID II Art. 57 position limits (since 28 February 2022 confined to agricultural
  commodity derivatives and critical or significant commodity derivatives) each have
  their own scope. Nothing here determines which applies to a given desk.
"""
import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: Internal default for pre-trade risk evaluation. NOT a regulatory threshold - no
#: regulator publishes one. Calibrate against the venue's throughput and record why.
DEFAULT_PRE_TRADE_LATENCY_SLA_MS = 5.0

#: RTS 6 Art. 16(5): real-time alerts within five seconds of the relevant event.
RTS6_ALERT_LATENCY_SLA_MS = 5000.0


class ProductionEnvironmentError(RuntimeError):
    """Raised when a drill is aimed at a production environment (RTS 6 Art. 10)."""


class DrillEnvironment(str, Enum):
    SANDBOX = "SANDBOX"
    STAGING = "STAGING"
    PAPER = "PAPER"
    PRODUCTION = "PRODUCTION"


#: Everything except PRODUCTION. Kept explicit so that adding an environment forces a
#: deliberate decision about whether drills may run there.
NON_PRODUCTION_ENVIRONMENTS = frozenset(
    {DrillEnvironment.SANDBOX, DrillEnvironment.STAGING, DrillEnvironment.PAPER}
)


class BreachType(str, Enum):
    EXCHANGE_LIMIT = "EXCHANGE_LIMIT"          # Exchange-set limit (e.g. CME Rule 559)
    BROKER_LIMIT = "BROKER_LIMIT"              # Clearing member / broker house limit
    ROGUE_ALGO = "ROGUE_ALGO"                  # Runaway strategy accumulating position
    ASSIGNMENT_OVERAGE = "ASSIGNMENT_OVERAGE"  # Rule 562 option-assignment grace
    EXEMPT_HEDGE = "EXEMPT_HEDGE"              # Rule 559 bona fide hedge exemption
    WITHIN_LIMIT = "WITHIN_LIMIT"              # Negative control, no breach at all


class ControlPhase(str, Enum):
    #: Order-entry control. RTS 6 Art. 15(5); SEC Rule 15c3-5(c)(1)(i).
    PRE_TRADE = "PRE_TRADE"
    #: Exposure control on positions already held. RTS 6 Art. 17(1) and 17(4).
    POST_TRADE = "POST_TRADE"


class ExpectedOutcome(str, Enum):
    #: Order must be rejected and the strategy disabled pending manual re-enable.
    BLOCK_AND_HALT = "BLOCK_AND_HALT"
    #: No order to reject; the control must alert and drive a remediation action.
    ALERT_ONLY = "ALERT_ONLY"
    #: Negative control - the control must not block or halt.
    ALLOW = "ALLOW"


class DrillStatus(str, Enum):
    CONTROL_VERIFIED = "CONTROL_VERIFIED"
    LATENCY_SLA_BREACHED = "LATENCY_SLA_BREACHED"
    CONTROL_FAILED = "CONTROL_FAILED"
    DRILL_SKIPPED_SIMULATOR_DISABLED = "DRILL_SKIPPED_SIMULATOR_DISABLED"


@dataclass
class FireDrillSimulatorConfig:
    """
    Drill harness configuration.

    ``max_pre_trade_latency_ms`` is an internal SLA, not a compliance threshold.
    ``max_alert_latency_ms`` defaults to the RTS 6 Art. 16(5) five-second alert
    requirement and applies to firms in scope of that regulation.
    """
    enabled: bool = True
    max_pre_trade_latency_ms: float = DEFAULT_PRE_TRADE_LATENCY_SLA_MS
    max_alert_latency_ms: float = RTS6_ALERT_LATENCY_SLA_MS
    require_negative_control: bool = True
    require_post_trade_scenario: bool = True
    parameters: Dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("max_pre_trade_latency_ms", "max_alert_latency_ms"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{name} must be a number, got {type(value).__name__}.")
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"{name} must be a finite positive number, got {value!r}.")


def _coerce(enum_cls, value, field_name: str):
    """Accepts either the enum member or its string value; rejects anything else."""
    try:
        return enum_cls(value)
    except ValueError:
        allowed = ", ".join(member.value for member in enum_cls)
        raise ValueError(f"{field_name} must be one of [{allowed}], got {value!r}.") from None


def _require_finite(value: float, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a number, got {type(value).__name__}.")
    if not math.isfinite(value):
        raise ValueError(
            f"{field_name} must be finite, got {value!r}. A NaN quantity compares false "
            f"against every limit and would silently be recorded as 'within limit'."
        )
    return float(value)


def is_over_limit(position_qty: float, limit_threshold: float) -> bool:
    """
    True when the position is *in excess of* the limit.

    Strict inequality is deliberate: CME Rule 562 deems violations for positions "in
    excess of those permitted", so a position exactly at the limit is compliant. A
    ``>=`` predicate would make every drill sitting exactly on the boundary expect a
    rejection that the gateway is correct not to issue.
    """
    qty = _require_finite(position_qty, "position_qty")
    limit = _require_finite(limit_threshold, "limit_threshold")
    return abs(qty) > limit


@dataclass(frozen=True)
class FireDrillScenario:
    """
    One injected drill case and the outcome that counts as correct for it.

    ``expected_outcome`` is the whole point: it is set from the rule that applies to the
    scenario, independently of what the system under test does, so that the comparison
    in ``run_fire_drill`` is able to disagree with the system.
    """
    scenario_id: str
    breach_type: BreachType
    target_symbol: str
    injected_position_qty: float
    limit_threshold: float
    environment: DrillEnvironment = DrillEnvironment.STAGING
    control_phase: ControlPhase = ControlPhase.PRE_TRADE
    expected_outcome: ExpectedOutcome = ExpectedOutcome.BLOCK_AND_HALT
    #: Required when an over-limit position is expected to be ALLOWed. Records the rule
    #: relied on (e.g. "CME Rule 562 option assignment: one business day to liquidate").
    exemption_basis: str = ""
    description: str = ""

    def __post_init__(self) -> None:
        if not str(self.scenario_id).strip():
            raise ValueError("scenario_id must be a non-empty string.")
        if not str(self.target_symbol).strip():
            raise ValueError("target_symbol must be a non-empty string.")

        object.__setattr__(
            self, "breach_type", _coerce(BreachType, self.breach_type, "breach_type"))
        object.__setattr__(
            self, "environment", _coerce(DrillEnvironment, self.environment, "environment"))
        object.__setattr__(
            self, "control_phase", _coerce(ControlPhase, self.control_phase, "control_phase"))
        object.__setattr__(
            self, "expected_outcome",
            _coerce(ExpectedOutcome, self.expected_outcome, "expected_outcome"))

        qty = _require_finite(self.injected_position_qty, "injected_position_qty")
        limit = _require_finite(self.limit_threshold, "limit_threshold")
        if limit <= 0.0:
            raise ValueError(f"limit_threshold must be positive, got {limit}.")
        object.__setattr__(self, "injected_position_qty", qty)
        object.__setattr__(self, "limit_threshold", limit)

        if self.expected_outcome is ExpectedOutcome.ALLOW and is_over_limit(qty, limit):
            if not str(self.exemption_basis).strip():
                raise ValueError(
                    f"Scenario {self.scenario_id!r} expects an over-limit position to be "
                    f"ALLOWed but records no exemption_basis. An unexplained allowance is "
                    f"indistinguishable from a gateway failure; cite the grace or exemption "
                    f"rule relied on (e.g. CME Rule 562 assignment grace, Rule 559 bona fide "
                    f"hedge filing, RTS 6 Art. 15(6) authorised override)."
                )
        if (self.control_phase is ControlPhase.POST_TRADE
                and self.expected_outcome is ExpectedOutcome.BLOCK_AND_HALT):
            raise ValueError(
                f"Scenario {self.scenario_id!r} is POST_TRADE but expects BLOCK_AND_HALT. A "
                f"post-trade control has no order to reject; use ALERT_ONLY."
            )

    @property
    def over_limit(self) -> bool:
        return is_over_limit(self.injected_position_qty, self.limit_threshold)


@dataclass(frozen=True)
class ObservedControlResponse:
    """
    What the system under test actually did. Supplied by the caller from gateway logs,
    kill-switch state and the alerting pipeline - never inferred from the scenario.
    """
    order_rejected: bool = False
    trading_halted: bool = False
    #: True when re-enabling the halted strategy requires a designated staff member
    #: (RTS 6 Art. 15(3)). A self-resuming halt is a finding.
    manual_reenable_required: bool = False
    #: Identifier of the compliance alert record. Empty means no alert was raised.
    compliance_alert_id: str = ""
    #: Remediation actually taken for a post-trade breach (RTS 6 Art. 17(1)).
    remediation_action: str = ""
    risk_latency_ms: Optional[float] = None
    alert_latency_ms: Optional[float] = None

    def __post_init__(self) -> None:
        for name in ("risk_latency_ms", "alert_latency_ms"):
            value = getattr(self, name)
            if value is None:
                continue
            latency = _require_finite(value, name)
            if latency < 0.0:
                raise ValueError(f"{name} must be non-negative, got {latency}.")
            object.__setattr__(self, name, latency)


@dataclass(frozen=True)
class FireDrillResult:
    scenario_id: str
    breach_type: str
    target_symbol: str
    environment: str
    control_phase: str
    expected_outcome: str
    injected_qty: float
    limit_threshold: float
    over_limit: bool
    order_rejected: bool
    trading_halted: bool
    compliance_alert_logged: bool
    measured_latency_ms: Optional[float]
    latency_sla_ms: Optional[float]
    latency_sla_passed: Optional[bool]
    passed: bool
    status: str
    findings: Tuple[str, ...]
    audit_notes: str


@dataclass(frozen=True)
class FireDrillSuiteReport:
    suite_id: str
    total_scenarios: int
    passed_count: int
    failed_count: int
    skipped_count: int
    negative_control_count: int
    post_trade_scenario_count: int
    all_passed: bool
    coverage_findings: Tuple[str, ...]
    results: Tuple[FireDrillResult, ...]


class FireDrillSimulator:
    """
    Grades position-limit breach fire drills.

    Usage is always two-sided: build the scenario from the rule, run the injection
    against the staging risk stack, then hand the harness both the scenario and the
    response the stack produced::

        sim = FireDrillSimulator()
        result = sim.run_fire_drill(scenario, observed)
        assert result.passed, result.findings
    """

    def __init__(self, config: Optional[FireDrillSimulatorConfig] = None) -> None:
        self.config = config or FireDrillSimulatorConfig()

    # ------------------------------------------------------------------ helpers

    def _latency_sla_for(self, scenario: FireDrillScenario) -> float:
        if scenario.control_phase is ControlPhase.PRE_TRADE:
            return self.config.max_pre_trade_latency_ms
        return self.config.max_alert_latency_ms

    @staticmethod
    def _measured_latency_for(
        scenario: FireDrillScenario, observed: ObservedControlResponse
    ) -> Optional[float]:
        if scenario.control_phase is ControlPhase.PRE_TRADE:
            return observed.risk_latency_ms
        return observed.alert_latency_ms

    @staticmethod
    def _behaviour_findings(
        scenario: FireDrillScenario, observed: ObservedControlResponse
    ) -> List[str]:
        """Compares observed behaviour against the scenario's expected outcome."""
        findings: List[str] = []
        alert_logged = bool(str(observed.compliance_alert_id).strip())

        if scenario.expected_outcome is ExpectedOutcome.BLOCK_AND_HALT:
            if not observed.order_rejected:
                findings.append(
                    "Pre-trade gateway did not reject an order that breaches the position "
                    "limit (RTS 6 Art. 15(5); SEC Rule 15c3-5(c)(1)(i))."
                )
            if not observed.trading_halted:
                findings.append(
                    "Kill switch did not halt the strategy after a position-limit breach."
                )
            if not alert_logged:
                findings.append("No compliance alert record was produced for the breach.")

        elif scenario.expected_outcome is ExpectedOutcome.ALERT_ONLY:
            if not alert_logged:
                findings.append(
                    "Post-trade exposure control produced no alert for an over-limit "
                    "position (RTS 6 Art. 17(1) and 17(4))."
                )
            if not str(observed.remediation_action).strip():
                findings.append(
                    "Post-trade breach alerted but no remediation action was recorded "
                    "(RTS 6 Art. 17(1): adjust, shut down, or withdraw in an orderly manner)."
                )

        elif scenario.expected_outcome is ExpectedOutcome.ALLOW:
            if observed.order_rejected:
                findings.append(
                    "Control rejected an order it should have allowed - over-blocking. "
                    f"Basis for allowance: {scenario.exemption_basis or 'within limit'}."
                )
            if observed.trading_halted:
                findings.append(
                    "Control halted trading on a scenario that should have been allowed."
                )

        if observed.trading_halted and not observed.manual_reenable_required:
            findings.append(
                "Halted strategy does not require a designated staff member to re-enable "
                "it (RTS 6 Art. 15(3)); a self-resuming halt is not a kill switch."
            )
        return findings

    # ---------------------------------------------------------------------- API

    def run_fire_drill(
        self,
        scenario: FireDrillScenario,
        observed: ObservedControlResponse,
    ) -> FireDrillResult:
        """
        Grades one drill.

        Raises ``ProductionEnvironmentError`` if the scenario targets production: RTS 6
        Art. 10 requires stress tests be carried out so that they do not affect the
        production environment, and a breach order that reaches a live matching engine
        is a real position-limit violation, not a drill.
        """
        if not isinstance(scenario, FireDrillScenario):
            raise TypeError(
                f"scenario must be a FireDrillScenario, got {type(scenario).__name__}.")
        if not isinstance(observed, ObservedControlResponse):
            raise TypeError(
                f"observed must be an ObservedControlResponse, got {type(observed).__name__}. "
                f"The harness grades recorded behaviour; it does not simulate the control."
            )
        if scenario.environment not in NON_PRODUCTION_ENVIRONMENTS:
            raise ProductionEnvironmentError(
                f"Scenario {scenario.scenario_id!r} targets {scenario.environment.value}. Fire "
                f"drills must not affect the production environment (RTS 6 Art. 10). Re-point "
                f"the scenario at SANDBOX, STAGING or PAPER."
            )

        if not self.config.enabled:
            notes = f"Drill {scenario.scenario_id} not executed: simulator disabled."
            logger.warning("%s", notes)
            return FireDrillResult(
                scenario_id=scenario.scenario_id,
                breach_type=scenario.breach_type.value,
                target_symbol=scenario.target_symbol,
                environment=scenario.environment.value,
                control_phase=scenario.control_phase.value,
                expected_outcome=scenario.expected_outcome.value,
                injected_qty=scenario.injected_position_qty,
                limit_threshold=scenario.limit_threshold,
                over_limit=scenario.over_limit,
                order_rejected=False,
                trading_halted=False,
                compliance_alert_logged=False,
                measured_latency_ms=None,
                latency_sla_ms=None,
                latency_sla_passed=None,
                passed=False,
                status=DrillStatus.DRILL_SKIPPED_SIMULATOR_DISABLED.value,
                findings=("Simulator disabled; the control was never exercised.",),
                audit_notes=notes,
            )

        findings = self._behaviour_findings(scenario, observed)
        behaviour_ok = not findings

        sla_ms = self._latency_sla_for(scenario)
        measured = self._measured_latency_for(scenario, observed)
        if measured is None:
            latency_passed: Optional[bool] = None
            findings.append(
                f"No {scenario.control_phase.value.lower()} latency was recorded, so the "
                f"SLA could not be evaluated; an unmeasured control is an unverified one."
            )
        else:
            latency_passed = measured <= sla_ms

        if not behaviour_ok or latency_passed is None:
            status = DrillStatus.CONTROL_FAILED
        elif latency_passed is False:
            status = DrillStatus.LATENCY_SLA_BREACHED
        else:
            status = DrillStatus.CONTROL_VERIFIED

        passed = status is DrillStatus.CONTROL_VERIFIED
        latency_text = "n/a" if measured is None else "{:.2f}ms".format(measured)

        notes = (
            f"POSITION LIMIT FIRE DRILL [{scenario.scenario_id} | {scenario.breach_type.value} "
            f"| {scenario.control_phase.value} | {scenario.environment.value}]: "
            f"{scenario.target_symbol} qty={scenario.injected_position_qty:,.0f} "
            f"limit={scenario.limit_threshold:,.0f} over_limit={scenario.over_limit}. "
            f"Expected={scenario.expected_outcome.value}. Observed: "
            f"rejected={observed.order_rejected}, halted={observed.trading_halted}, "
            f"alert={observed.compliance_alert_id or 'NONE'}, latency={latency_text} "
            f"(SLA {sla_ms:.2f}ms). Status={status.value}."
        )

        if passed:
            logger.info("FIRE DRILL PASS: %s", notes)
        else:
            logger.error("FIRE DRILL FAIL: %s Findings: %s", notes, "; ".join(findings))

        return FireDrillResult(
            scenario_id=scenario.scenario_id,
            breach_type=scenario.breach_type.value,
            target_symbol=scenario.target_symbol,
            environment=scenario.environment.value,
            control_phase=scenario.control_phase.value,
            expected_outcome=scenario.expected_outcome.value,
            injected_qty=scenario.injected_position_qty,
            limit_threshold=scenario.limit_threshold,
            over_limit=scenario.over_limit,
            order_rejected=observed.order_rejected,
            trading_halted=observed.trading_halted,
            compliance_alert_logged=bool(str(observed.compliance_alert_id).strip()),
            measured_latency_ms=measured,
            latency_sla_ms=sla_ms,
            latency_sla_passed=latency_passed,
            passed=passed,
            status=status.value,
            findings=tuple(findings),
            audit_notes=notes,
        )

    def run_drill_suite(
        self,
        suite_id: str,
        cases: Sequence[Tuple[FireDrillScenario, ObservedControlResponse]],
    ) -> FireDrillSuiteReport:
        """
        Grades a full drill suite and audits its coverage.

        ``all_passed`` requires every case to pass *and* the suite to cover both a
        negative control and a post-trade scenario. Those coverage gates exist because a
        suite made only of "inject a breach, expect a block" cases cannot detect either a
        gateway that blocks everything or a breach that arrives without an order (option
        assignment, close-of-day delta re-evaluation, a scheduled spot-month limit
        step-down, or exposure aggregated across venues).
        """
        if not str(suite_id).strip():
            raise ValueError("suite_id must be a non-empty string.")
        case_list = list(cases)
        if not case_list:
            raise ValueError("A drill suite must contain at least one scenario.")

        seen: Dict[str, int] = {}
        results: List[FireDrillResult] = []
        for index, case in enumerate(case_list):
            try:
                scenario, observed = case
            except (TypeError, ValueError):
                raise TypeError(
                    f"cases[{index}] must be a (FireDrillScenario, ObservedControlResponse) pair."
                ) from None
            if scenario.scenario_id in seen:
                raise ValueError(
                    f"Duplicate scenario_id {scenario.scenario_id!r} at positions "
                    f"{seen[scenario.scenario_id]} and {index}; drill records must be "
                    f"individually attributable."
                )
            seen[scenario.scenario_id] = index
            results.append(self.run_fire_drill(scenario, observed))

        negative_controls = sum(
            1 for scenario, _ in case_list
            if scenario.expected_outcome is ExpectedOutcome.ALLOW
        )
        post_trade = sum(
            1 for scenario, _ in case_list
            if scenario.control_phase is ControlPhase.POST_TRADE
        )

        coverage: List[str] = []
        if self.config.require_negative_control and negative_controls == 0:
            coverage.append(
                "Suite contains no ALLOW negative control; it cannot detect a gateway that "
                "over-blocks compliant or exempt positions."
            )
        if self.config.require_post_trade_scenario and post_trade == 0:
            coverage.append(
                "Suite contains no POST_TRADE scenario; it cannot detect a breach that "
                "arrives without an order to reject (RTS 6 Art. 17(4); CME Rule 562 "
                "assignment and delta-re-evaluation cases)."
            )

        skipped = sum(
            1 for r in results
            if r.status == DrillStatus.DRILL_SKIPPED_SIMULATOR_DISABLED.value
        )
        passed_count = sum(1 for r in results if r.passed)
        failed_count = len(results) - passed_count - skipped
        all_passed = passed_count == len(results) and not coverage

        if all_passed:
            logger.info(
                "FIRE DRILL SUITE PASS [%s]: %d/%d scenarios verified.",
                suite_id, passed_count, len(results),
            )
        else:
            logger.error(
                "FIRE DRILL SUITE FAIL [%s]: %d passed, %d failed, %d skipped, "
                "%d coverage gap(s).",
                suite_id, passed_count, failed_count, skipped, len(coverage),
            )

        return FireDrillSuiteReport(
            suite_id=suite_id,
            total_scenarios=len(results),
            passed_count=passed_count,
            failed_count=failed_count,
            skipped_count=skipped,
            negative_control_count=negative_controls,
            post_trade_scenario_count=post_trade,
            all_passed=all_passed,
            coverage_findings=tuple(coverage),
            results=tuple(results),
        )
