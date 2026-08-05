import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable

logger = logging.getLogger(__name__)

@dataclass
class InputData:
    """Legacy InputData class for backward compatibility."""
    data: str
    value: float

class RiskUnitTestHarness:
    """Legacy class retained for backward compatibility."""
    def __init__(self):
        self.history: List[InputData] = []

    def process(self, data: InputData) -> bool:
        self.history.append(data)
        if data.value < 0:
            return False
        return True

@dataclass
class RiskRuleConfig:
    rule_id: str                         # e.g. 'MAX_ORDER_SIZE', 'POSITION_CAP', 'FAT_FINGER_PRICE_COLLAR', 'DAILY_LOSS_LIMIT'
    enabled: bool = True
    max_order_size: float = 1000.0
    max_position_size: float = 5000.0
    max_daily_loss_usd: float = 10000.0
    max_price_collar_pct: float = 0.05    # 5% collar from mid price

@dataclass
class ProposedOrder:
    order_id: str
    symbol: str
    side: str                            # 'BUY', 'SELL'
    quantity: float
    price: float
    current_mid_price: float = 100.0
    current_position: float = 0.0
    accumulated_daily_pnl_usd: float = 0.0

@dataclass
class RiskCheckResult:
    order_id: str
    is_allowed: bool
    triggered_rules: List[str]
    rejection_reasons: List[str]
    latency_microseconds: float

@dataclass
class TestCaseResult:
    test_name: str
    expected_allowed: bool
    actual_allowed: bool
    expected_triggered_rule: Optional[str]
    passed: bool
    detail: str

@dataclass
class RiskControlTestExecutionReport:
    total_tests: int
    passed_tests: int
    failed_tests: int
    test_results: List[TestCaseResult]
    pass_rate_pct: float
    status: str                          # 'ALL_RISK_TESTS_PASSED', 'RISK_TEST_FAILURES_DETECTED'
    audit_notes: str

class PreTradeRiskEngine:
    """Sample pre-trade risk engine evaluated by the unit testing framework."""
    def __init__(self, config: RiskRuleConfig):
        self.config = config

    def evaluate_order(self, order: ProposedOrder) -> RiskCheckResult:
        t0 = time.perf_counter_ns()
        triggered = []
        reasons = []

        if not self.config.enabled:
            dt = (time.perf_counter_ns() - t0) / 1000.0
            return RiskCheckResult(order.order_id, True, [], [], dt)

        # 1. Max Order Size Check
        if order.quantity > self.config.max_order_size:
            triggered.append("MAX_ORDER_SIZE")
            reasons.append(f"Quantity {order.quantity} > Max allowed {self.config.max_order_size}")

        # 2. Position Cap Check
        projected = order.current_position + (order.quantity if order.side.upper() == "BUY" else -order.quantity)
        if abs(projected) > self.config.max_position_size:
            triggered.append("POSITION_CAP")
            reasons.append(f"Projected position {projected} > Max cap {self.config.max_position_size}")

        # 3. Fat-Finger Price Collar Check
        if order.current_mid_price > 0:
            pct_diff = abs(order.price - order.current_mid_price) / order.current_mid_price
            if pct_diff > self.config.max_price_collar_pct:
                triggered.append("FAT_FINGER_PRICE_COLLAR")
                reasons.append(f"Price deviation {pct_diff:.2%} > Max allowed {self.config.max_price_collar_pct:.2%}")

        # 4. Daily Loss Limit Check
        if order.accumulated_daily_pnl_usd < -self.config.max_daily_loss_usd:
            triggered.append("DAILY_LOSS_LIMIT")
            reasons.append(f"Daily PnL {order.accumulated_daily_pnl_usd} breached loss limit -{self.config.max_daily_loss_usd}")

        dt = (time.perf_counter_ns() - t0) / 1000.0
        is_allowed = len(triggered) == 0

        return RiskCheckResult(
            order_id=order.order_id,
            is_allowed=is_allowed,
            triggered_rules=triggered,
            rejection_reasons=reasons,
            latency_microseconds=dt
        )

class RiskControlUnitTestFrameworkEngine:
    """
    Dedicated unit testing framework engine for pre-trade risk controls executing automated scenario suites
    (positive orders, limit breaches, boundary edge cases, price collars) and asserting deterministic risk behavior.
    """
    def __init__(self, risk_engine: Optional[PreTradeRiskEngine] = None):
        cfg = RiskRuleConfig(
            rule_id="DEFAULT_RULESET",
            max_order_size=1000.0,
            max_position_size=5000.0,
            max_daily_loss_usd=10000.0,
            max_price_collar_pct=0.05
        )
        self.risk_engine = risk_engine or PreTradeRiskEngine(cfg)

    def run_test_case(
        self,
        test_name: str,
        order: ProposedOrder,
        expected_allowed: bool,
        expected_triggered_rule: Optional[str] = None
    ) -> TestCaseResult:
        """Executes a single risk rule test case and validates expected output."""
        res = self.risk_engine.evaluate_order(order)
        allowed_matches = (res.is_allowed == expected_allowed)

        rule_matches = True
        if expected_triggered_rule:
            rule_matches = expected_triggered_rule in res.triggered_rules

        passed = allowed_matches and rule_matches

        if passed:
            detail = f"PASSED: Allowed={res.is_allowed}, Triggered={res.triggered_rules}"
        else:
            detail = (
                f"FAILED: Expected allowed={expected_allowed}, got {res.is_allowed}. "
                f"Expected rule={expected_triggered_rule}, got {res.triggered_rules}"
            )

        return TestCaseResult(
            test_name=test_name,
            expected_allowed=expected_allowed,
            actual_allowed=res.is_allowed,
            expected_triggered_rule=expected_triggered_rule,
            passed=passed,
            detail=detail
        )

    def run_standard_suite(self) -> RiskControlTestExecutionReport:
        """Executes a standard suite of pre-trade risk rule unit tests."""
        tests = [
            # 1. Normal order -> SHOULD PASS
            ("Normal Valid Order", ProposedOrder("O1", "AAPL", "BUY", 100.0, 150.0, current_mid_price=150.0), True, None),
            # 2. Max order size breach -> SHOULD REJECT (MAX_ORDER_SIZE)
            ("Max Order Size Breach", ProposedOrder("O2", "AAPL", "BUY", 2000.0, 150.0, current_mid_price=150.0), False, "MAX_ORDER_SIZE"),
            # 3. Position cap breach -> SHOULD REJECT (POSITION_CAP)
            ("Position Cap Breach", ProposedOrder("O3", "AAPL", "BUY", 900.0, 150.0, current_position=4500.0, current_mid_price=150.0), False, "POSITION_CAP"),
            # 4. Fat-finger price collar breach -> SHOULD REJECT (FAT_FINGER_PRICE_COLLAR)
            ("Fat Finger Price Collar", ProposedOrder("O4", "AAPL", "BUY", 100.0, 200.0, current_mid_price=150.0), False, "FAT_FINGER_PRICE_COLLAR"),
            # 5. Daily loss limit breach -> SHOULD REJECT (DAILY_LOSS_LIMIT)
            ("Daily Loss Limit Breach", ProposedOrder("O5", "AAPL", "BUY", 100.0, 150.0, accumulated_daily_pnl_usd=-15000.0, current_mid_price=150.0), False, "DAILY_LOSS_LIMIT"),
        ]

        results = []
        passed_count = 0

        for tname, order, exp_allowed, exp_rule in tests:
            tc_res = self.run_test_case(tname, order, exp_allowed, exp_rule)
            results.append(tc_res)
            if tc_res.passed:
                passed_count += 1

        total = len(results)
        failed_count = total - passed_count
        pass_rate = round((passed_count / total) * 100.0, 2) if total > 0 else 0.0

        status = "ALL_RISK_TESTS_PASSED" if failed_count == 0 else "RISK_TEST_FAILURES_DETECTED"
        notes = f"RISK TEST SUITE [{status}]: Passed {passed_count}/{total} ({pass_rate}%)."

        if failed_count > 0:
            logger.warning(notes)
        else:
            logger.info(notes)

        return RiskControlTestExecutionReport(
            total_tests=total,
            passed_tests=passed_count,
            failed_tests=failed_count,
            test_results=results,
            pass_rate_pct=pass_rate,
            status=status,
            audit_notes=notes
        )
