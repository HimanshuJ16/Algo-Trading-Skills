import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class ScenarioTestResult:
    scenario_id: str
    scenario_name: str                  # e.g. 'NORMAL_VOLATILITY', 'VOLATILITY_SHOCK', 'LIQUIDITY_CRUNCH'
    baseline_is_bps: float
    candidate_is_bps: float
    baseline_fill_rate: float
    candidate_fill_rate: float
    candidate_max_participation_rate: float
    passed: bool
    failure_reason: Optional[str] = None

@dataclass
class RegressionTestSuiteAuditReport:
    algo_name: str
    build_version: str
    total_scenarios_tested: int
    scenarios_passed_count: int
    scenarios_failed_count: int
    avg_is_degradation_bps: float       # candidate_is - baseline_is
    cicd_gate_status: str               # 'PASS_REGRESSION_APPROVED', 'FAIL_REGRESSION_REJECTED'
    scenario_details: List[ScenarioTestResult]
    audit_notes: str

class ExecutionAlgoRegressionTestSuite:
    """
    Quantitative CI/CD testing engine for executing automated regression benchmarks (MiFID II RTS 6, SEC 15c3-5)
    on execution algorithm code updates, comparing Implementation Shortfall, fill rates, and risk bounds.
    """
    def __init__(
        self,
        max_allowed_is_degradation_bps: float = 2.0,   # Max +2.0 bps increase in IS allowed
        min_allowed_fill_ratio: float = 0.98,          # Candidate fill rate must be >= 98% of baseline
        max_allowed_participation_rate: float = 0.20   # Hard 20% ADV limit
    ):
        self.max_allowed_is_degradation_bps = max_allowed_is_degradation_bps
        self.min_allowed_fill_ratio = min_allowed_fill_ratio
        self.max_allowed_participation_rate = max_allowed_participation_rate

    def run_regression_suite(
        self,
        algo_name: str,
        build_version: str,
        scenarios: List[ScenarioTestResult]
    ) -> RegressionTestSuiteAuditReport:
        """
        Audits candidate code build against RTS 6 regression standards across historical market scenarios.
        """
        if not scenarios:
            raise ValueError("Scenario test results list cannot be empty.")

        passed_cnt = 0
        failed_cnt = 0
        is_degradations: List[float] = []
        audited_scenarios: List[ScenarioTestResult] = []

        for sc in scenarios:
            is_diff = round(sc.candidate_is_bps - sc.baseline_is_bps, 2)
            is_degradations.append(is_diff)

            fill_ratio = round(sc.candidate_fill_rate / max(1e-4, sc.baseline_fill_rate), 4)

            fail_reasons: List[str] = []

            # Rule 1: Shortfall Degradation Check
            if is_diff > self.max_allowed_is_degradation_bps:
                fail_reasons.append(f"IS degradation {is_diff:+.1f}bps exceeds allowed max +{self.max_allowed_is_degradation_bps:.1f}bps")

            # Rule 2: Fill Rate Ratio Check
            if fill_ratio < self.min_allowed_fill_ratio:
                fail_reasons.append(f"Fill rate ratio {fill_ratio:.2f} below allowed min {self.min_allowed_fill_ratio:.2f}")

            # Rule 3: Max Participation Rate Limit Check
            if sc.candidate_max_participation_rate > self.max_allowed_participation_rate:
                fail_reasons.append(f"Max participation {sc.candidate_max_participation_rate:.2f} exceeds limit {self.max_allowed_participation_rate:.2f}")

            if fail_reasons:
                sc.passed = False
                sc.failure_reason = "; ".join(fail_reasons)
                failed_cnt += 1
                logger.error(f"REGRESSION SCENARIO FAILED [{sc.scenario_name}]: {sc.failure_reason}")
            else:
                sc.passed = True
                sc.failure_reason = None
                passed_cnt += 1

            audited_scenarios.append(sc)

        avg_degradation = round(sum(is_degradations) / float(len(is_degradations)), 2)

        if failed_cnt > 0:
            gate_status = "FAIL_REGRESSION_REJECTED"
            notes = (
                f"CI/CD REGRESSION GATE FAILED [{algo_name} build {build_version}]: {failed_cnt}/{len(scenarios)} scenarios FAILED. "
                f"Average IS degradation = {avg_degradation:+.2f}bps. Build REJECTED for deployment."
            )
            logger.critical(notes)
        else:
            gate_status = "PASS_REGRESSION_APPROVED"
            notes = (
                f"CI/CD REGRESSION GATE PASSED [{algo_name} build {build_version}]: All {len(scenarios)} scenarios PASSED RTS 6 benchmarks. "
                f"Average IS change = {avg_degradation:+.2f}bps. Build APPROVED for deployment."
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
            audit_notes=notes
        )
