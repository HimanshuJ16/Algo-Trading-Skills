"""
Fail-closed CI/CD regression gate for execution-algorithm code changes.

Replays a candidate build and a recorded production baseline over a fixed suite of
market scenarios, compares Implementation Shortfall, fill-rate ratio and peak
participation, and returns a deploy/no-deploy verdict plus an auditable report.

SCOPE - read before quoting this module in a compliance pack. This is a
*performance-regression* gate: it answers "did this build get measurably worse than
the baseline on the scenarios we replayed?" It does NOT demonstrate that an algorithm
avoids contributing to disorderly trading conditions, and a PASS verdict is not on its
own evidence of MiFID II RTS 6 Article 5 compliance. The FCA names benchmark-only
testing as poor practice. See `references/standards.md` for the sourced detail.
"""
import logging
import math
from dataclasses import dataclass, replace
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

PASS_REGRESSION_APPROVED = "PASS_REGRESSION_APPROVED"
FAIL_REGRESSION_REJECTED = "FAIL_REGRESSION_REJECTED"

#: Scenario kinds the suite requires by default. These are this library's convention,
#: NOT a regulatory list - ESMA expressly declined to prescribe which tests a firm must
#: run (ESMA70-156-4572, paras 187-188). Override to match your own scenario taxonomy;
#: pass an empty sequence to disable coverage enforcement entirely.
DEFAULT_REQUIRED_SCENARIOS: Tuple[str, ...] = (
    "NORMAL_VOLATILITY",
    "VOLATILITY_SHOCK",
    "LIQUIDITY_CRUNCH",
)


def _fmt(value: float, *, signed: bool = False) -> str:
    """
    Render a metric without hiding a threshold breach behind display rounding.

    A degradation of +2.004 bps against a +2.0 bps limit must not print as "+2.0",
    which would make the failure message read as self-contradictory.
    """
    text = f"{value:+.4f}" if signed else f"{value:.4f}"
    text = text.rstrip("0")
    return text + "0" if text.endswith(".") else text


@dataclass
class ScenarioTestResult:
    """
    One replayed scenario: baseline metrics, candidate metrics, and the engine's verdict.

    Caller-supplied inputs:
      scenario_id                       unique within a suite run
      scenario_name                     scenario kind, e.g. 'VOLATILITY_SHOCK'
      baseline_is_bps/candidate_is_bps  Implementation Shortfall in basis points, signed
                                        so that a LARGER value is a WORSE execution
                                        (a cost, not a P&L)
      baseline_fill_rate                filled/ordered quantity, in (0.0, 1.0]
      candidate_fill_rate               filled/ordered quantity, in [0.0, 1.0]
      candidate_max_participation_rate  peak share of market volume consumed, in [0.0, 1.0]

    Engine outputs (do not populate these; `run_regression_suite` returns fresh copies
    and never mutates the objects you pass in):
      passed, failure_reason, is_degradation_bps, fill_rate_ratio

    `is_degradation_bps` is None when the shortfall comparison was not evaluable.
    """
    scenario_id: str
    scenario_name: str                  # e.g. 'NORMAL_VOLATILITY', 'VOLATILITY_SHOCK', 'LIQUIDITY_CRUNCH'
    baseline_is_bps: float
    candidate_is_bps: float
    baseline_fill_rate: float
    candidate_fill_rate: float
    candidate_max_participation_rate: float
    passed: bool = False
    failure_reason: Optional[str] = None
    is_degradation_bps: Optional[float] = None
    fill_rate_ratio: Optional[float] = None


@dataclass
class RegressionTestSuiteAuditReport:
    """
    Deployment verdict for one candidate build.

    `avg_is_degradation_bps` and `worst_is_degradation_bps` are computed over the
    scenarios whose shortfall comparison was evaluable, and are None when none were.
    A missing required scenario rejects the build with `scenarios_failed_count == 0`;
    always read `cicd_gate_status`, never infer approval from the failure count.
    """
    algo_name: str
    build_version: str
    total_scenarios_tested: int
    scenarios_passed_count: int
    scenarios_failed_count: int
    avg_is_degradation_bps: Optional[float]     # mean(candidate_is - baseline_is), evaluable scenarios only
    cicd_gate_status: str                       # 'PASS_REGRESSION_APPROVED', 'FAIL_REGRESSION_REJECTED'
    scenario_details: List[ScenarioTestResult]
    audit_notes: str
    worst_is_degradation_bps: Optional[float] = None
    missing_required_scenarios: Optional[List[str]] = None
    coverage_satisfied: bool = True


def _as_float(label: str, value: object) -> float:
    """Reject bools and non-numerics before any arithmetic or comparison."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be a real number, got {type(value).__name__}.")
    return float(value)


def _validate_scenario(scenario: ScenarioTestResult) -> None:
    """
    Reject structurally invalid harness output.

    A malformed scenario means the replay harness is mis-wired, not that the candidate
    regressed, so it raises rather than silently producing a verdict. Non-finite
    Implementation Shortfall is deliberately NOT rejected here: it is a symptom of the
    candidate build, and is reported as a scenario failure so it reaches the audit trail.
    """
    if not isinstance(scenario.scenario_id, str) or not scenario.scenario_id.strip():
        raise ValueError("scenario_id must be a non-empty string.")
    tag = scenario.scenario_id.strip()

    if not isinstance(scenario.scenario_name, str) or not scenario.scenario_name.strip():
        raise ValueError(f"[{tag}] scenario_name must be a non-empty string.")

    _as_float(f"[{tag}] baseline_is_bps", scenario.baseline_is_bps)
    _as_float(f"[{tag}] candidate_is_bps", scenario.candidate_is_bps)

    for field_name in ("baseline_fill_rate", "candidate_fill_rate",
                       "candidate_max_participation_rate"):
        value = _as_float(f"[{tag}] {field_name}", getattr(scenario, field_name))
        if not math.isfinite(value):
            raise ValueError(f"[{tag}] {field_name} must be finite, got {value}.")
        if not 0.0 <= value <= 1.0:
            raise ValueError(
                f"[{tag}] {field_name} must be a fraction in [0.0, 1.0], got {value}. "
                "Fill and participation rates are fractions, not percentages."
            )

    if scenario.baseline_fill_rate <= 0.0:
        raise ValueError(
            f"[{tag}] baseline_fill_rate is 0.0: the fill-rate ratio is undefined against "
            "a baseline that filled nothing. Fix the baseline replay or remove this "
            "scenario - do not substitute a floor, which turns any candidate fill rate "
            "into an arbitrarily large passing ratio."
        )


class ExecutionAlgoRegressionTestSuite:
    """
    Compares a candidate execution-algorithm build against a recorded production baseline
    across replayed market scenarios and issues a fail-closed CI/CD deployment verdict.

    Every threshold is a configurable engineering default. No regulator publishes a
    maximum tolerable shortfall degradation, a minimum fill-rate ratio, or a maximum
    participation rate; calibrate these against your own venues, instruments and mandate,
    and record the rationale.
    """

    def __init__(
        self,
        max_allowed_is_degradation_bps: float = 2.0,   # Max +2.0 bps increase in IS allowed
        min_allowed_fill_ratio: float = 0.98,          # Candidate fill rate must be >= 98% of baseline
        max_allowed_participation_rate: float = 0.20,  # Peak share of market volume the candidate may consume
        required_scenario_names: Sequence[str] = DEFAULT_REQUIRED_SCENARIOS,
    ):
        max_allowed_is_degradation_bps = _as_float(
            "max_allowed_is_degradation_bps", max_allowed_is_degradation_bps)
        if not math.isfinite(max_allowed_is_degradation_bps):
            raise ValueError("max_allowed_is_degradation_bps must be finite.")

        min_allowed_fill_ratio = _as_float("min_allowed_fill_ratio", min_allowed_fill_ratio)
        if not math.isfinite(min_allowed_fill_ratio) or min_allowed_fill_ratio <= 0.0:
            raise ValueError("min_allowed_fill_ratio must be a positive finite ratio.")

        max_allowed_participation_rate = _as_float(
            "max_allowed_participation_rate", max_allowed_participation_rate)
        if not math.isfinite(max_allowed_participation_rate) or not (
                0.0 < max_allowed_participation_rate <= 1.0):
            raise ValueError("max_allowed_participation_rate must be a fraction in (0.0, 1.0].")

        if isinstance(required_scenario_names, str):
            # Iterating a bare string yields characters, which would silently turn
            # "VOLATILITY_SHOCK" into 16 single-letter requirements no suite can satisfy.
            raise TypeError(
                "required_scenario_names must be a sequence of names, not a single string. "
                "Pass a tuple, e.g. ('VOLATILITY_SHOCK',).")

        self.max_allowed_is_degradation_bps = max_allowed_is_degradation_bps
        self.min_allowed_fill_ratio = min_allowed_fill_ratio
        self.max_allowed_participation_rate = max_allowed_participation_rate
        # dict.fromkeys de-duplicates while preserving declaration order, so a repeated
        # requirement cannot be reported as missing twice.
        self.required_scenario_names: Tuple[str, ...] = tuple(dict.fromkeys(
            name.strip().upper() for name in required_scenario_names if name and name.strip()
        ))

        if not self.required_scenario_names:
            logger.warning(
                "Scenario coverage enforcement is DISABLED: a build can now be approved on a "
                "single quiet-market scenario, which does not evidence stressed-condition "
                "testing (MiFID II RTS 6 Art. 5(4)(d))."
            )

    def run_regression_suite(
        self,
        algo_name: str,
        build_version: str,
        scenarios: List[ScenarioTestResult]
    ) -> RegressionTestSuiteAuditReport:
        """
        Audits a candidate code build against the configured regression thresholds.

        Fails closed: a metric that cannot be evaluated, or a missing required scenario,
        rejects the build. Never mutates `scenarios` - the report carries fresh copies.

        Raises ValueError/TypeError on structurally invalid harness output (empty suite,
        duplicate scenario ids, out-of-range rates, non-numeric metrics).
        """
        if not scenarios:
            raise ValueError("Scenario test results list cannot be empty.")

        # The report is the deliverable evidence; an unlabelled one cannot be traced back
        # to the build it cleared.
        for label, value in (("algo_name", algo_name), ("build_version", build_version)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"{label} must be a non-empty string so the audit report identifies the "
                    "build it approved or rejected.")

        seen_ids = set()
        for scenario in scenarios:
            _validate_scenario(scenario)
            key = scenario.scenario_id.strip()
            if key in seen_ids:
                raise ValueError(
                    f"Duplicate scenario_id '{key}': scenario ids must be unique within a suite "
                    "run, otherwise a report line cannot be traced back to the replay that "
                    "produced it."
                )
            seen_ids.add(key)

        passed_cnt = 0
        failed_cnt = 0
        is_degradations: List[float] = []
        audited_scenarios: List[ScenarioTestResult] = []

        for sc in scenarios:
            fail_reasons: List[str] = []

            is_diff = sc.candidate_is_bps - sc.baseline_is_bps
            if math.isfinite(is_diff):
                is_degradations.append(is_diff)
                reported_is_diff: Optional[float] = round(is_diff, 4)

                # Rule 1: Shortfall Degradation Check (compared exact, displayed rounded)
                if is_diff > self.max_allowed_is_degradation_bps:
                    fail_reasons.append(
                        f"IS degradation {_fmt(is_diff, signed=True)}bps exceeds allowed max "
                        f"{_fmt(self.max_allowed_is_degradation_bps, signed=True)}bps")
            else:
                reported_is_diff = None
                fail_reasons.append(
                    f"Implementation Shortfall is not a finite number "
                    f"(baseline={sc.baseline_is_bps}, candidate={sc.candidate_is_bps}); the "
                    "shortfall comparison cannot be evaluated, so the build is rejected")

            # Rule 2: Fill Rate Ratio Check
            fill_ratio = sc.candidate_fill_rate / sc.baseline_fill_rate
            if fill_ratio < self.min_allowed_fill_ratio:
                fail_reasons.append(
                    f"Fill rate ratio {_fmt(fill_ratio)} below allowed min "
                    f"{_fmt(self.min_allowed_fill_ratio)}")

            # Rule 3: Max Participation Rate Limit Check
            if sc.candidate_max_participation_rate > self.max_allowed_participation_rate:
                fail_reasons.append(
                    f"Max participation {_fmt(sc.candidate_max_participation_rate)} exceeds "
                    f"limit {_fmt(self.max_allowed_participation_rate)}")

            verdict = replace(
                sc,
                passed=not fail_reasons,
                failure_reason="; ".join(fail_reasons) if fail_reasons else None,
                is_degradation_bps=reported_is_diff,
                fill_rate_ratio=round(fill_ratio, 4),
            )
            if fail_reasons:
                failed_cnt += 1
                logger.error("REGRESSION SCENARIO FAILED [%s]: %s",
                             verdict.scenario_name, verdict.failure_reason)
            else:
                passed_cnt += 1

            audited_scenarios.append(verdict)

        avg_degradation = (round(sum(is_degradations) / float(len(is_degradations)), 2)
                           if is_degradations else None)
        worst_degradation = round(max(is_degradations), 2) if is_degradations else None

        present_names = {sc.scenario_name.strip().upper() for sc in scenarios}
        missing = [name for name in self.required_scenario_names if name not in present_names]

        avg_text = "n/a" if avg_degradation is None else f"{avg_degradation:+.2f}bps"
        worst_text = "n/a" if worst_degradation is None else f"{worst_degradation:+.2f}bps"
        scope_note = (
            "Scope: performance-regression evidence only; it does not demonstrate that the "
            "algorithm avoids contributing to disorderly trading conditions "
            "(MiFID II RTS 6 Art. 5(4)(d))."
        )

        if failed_cnt > 0 or missing:
            gate_status = FAIL_REGRESSION_REJECTED
            coverage_text = (f" Required scenarios missing from the suite: {', '.join(missing)}."
                             if missing else "")
            notes = (
                f"CI/CD REGRESSION GATE FAILED [{algo_name} build {build_version}]: "
                f"{failed_cnt}/{len(scenarios)} scenarios FAILED.{coverage_text} "
                f"Average IS degradation = {avg_text}, worst = {worst_text}. "
                f"Build REJECTED for deployment. {scope_note}"
            )
            logger.critical(notes)
        else:
            gate_status = PASS_REGRESSION_APPROVED
            notes = (
                f"CI/CD REGRESSION GATE PASSED [{algo_name} build {build_version}]: all "
                f"{len(scenarios)} replayed scenarios met the configured regression thresholds. "
                f"Average IS change = {avg_text}, worst = {worst_text}. "
                f"Build APPROVED for deployment. {scope_note}"
            )
            logger.info(notes)

        return RegressionTestSuiteAuditReport(
            algo_name=algo_name,
            build_version=build_version,
            total_scenarios_tested=len(scenarios),
            scenarios_passed_count=passed_cnt,
            scenarios_failed_count=failed_cnt,
            avg_is_degradation_bps=avg_degradation,
            cicd_gate_status=gate_status,
            scenario_details=audited_scenarios,
            audit_notes=notes,
            worst_is_degradation_bps=worst_degradation,
            missing_required_scenarios=missing,
            coverage_satisfied=not missing,
        )
