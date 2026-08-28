"""Unit-testing framework for pre-trade risk controls.

The framework executes a suite of :class:`RiskTestCase` objects against a pre-trade
risk engine and returns an auditable :class:`RiskControlTestExecutionReport` suitable
for use as a CI/CD release gate.

Two failure classes are deliberately kept apart:

* **Harness mis-wiring** (blank/duplicate test names, a config with a non-positive
  order-size limit, an expectation that asserts both "allowed" and "rule X fired")
  raises ``ValueError``/``TypeError``. A mis-wired harness must not emit a verdict.
* **Risk decisions** (including malformed order data and an unusable reference price)
  are *rejections* produced by the engine, because a malformed order genuinely arrives
  at a live pre-trade gate and must be blocked there rather than crash the gate.

Threshold convention, applied uniformly by :class:`PreTradeRiskEngine`: the configured
limit value is itself permitted; a breach requires *exceeding* it. An order of exactly
``max_order_size`` passes; ``max_order_size`` plus any amount does not.
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# Rule identifiers emitted by PreTradeRiskEngine.
RULE_MAX_ORDER_SIZE = "MAX_ORDER_SIZE"
RULE_POSITION_CAP = "POSITION_CAP"
RULE_FAT_FINGER_PRICE_COLLAR = "FAT_FINGER_PRICE_COLLAR"
RULE_DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
RULE_INVALID_ORDER = "INVALID_ORDER"
RULE_REFERENCE_PRICE_UNAVAILABLE = "REFERENCE_PRICE_UNAVAILABLE"

#: Rules a standard suite must exercise before the gate may report a pass.
DEFAULT_REQUIRED_RULE_COVERAGE: Tuple[str, ...] = (
    RULE_MAX_ORDER_SIZE,
    RULE_POSITION_CAP,
    RULE_FAT_FINGER_PRICE_COLLAR,
    RULE_DAILY_LOSS_LIMIT,
    RULE_INVALID_ORDER,
    RULE_REFERENCE_PRICE_UNAVAILABLE,
)

_VALID_SIDES = frozenset({"BUY", "SELL"})

STATUS_PASSED = "ALL_RISK_TESTS_PASSED"
STATUS_FAILURES = "RISK_TEST_FAILURES_DETECTED"
STATUS_COVERAGE_INCOMPLETE = "RISK_TEST_COVERAGE_INCOMPLETE"
STATUS_SUITE_EMPTY = "RISK_TEST_SUITE_EMPTY"
STATUS_LATENCY_BREACH = "RISK_TEST_LATENCY_BUDGET_EXCEEDED"


def _is_real_number(value: Any) -> bool:
    """True only for a finite int/float. Rejects bool, NaN, +/-inf and non-numerics."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def _percentile(samples: Sequence[float], pct: float) -> float:
    """Nearest-rank percentile. Avoids a numpy dependency for suite-sized samples."""
    if not samples:
        return 0.0
    ordered = sorted(samples)
    rank = math.ceil((pct / 100.0) * len(ordered))
    idx = min(max(rank - 1, 0), len(ordered) - 1)
    return ordered[idx]


@dataclass(frozen=True)
class RiskRuleConfig:
    """Pre-trade limit set. Frozen so a suite cannot mutate limits between cases."""

    rule_id: str
    enabled: bool = True
    max_order_size: float = 1000.0
    max_position_size: float = 5000.0
    max_daily_loss_usd: float = 10000.0
    max_price_collar_pct: float = 0.05  # fractional deviation from the reference price

    def __post_init__(self) -> None:
        if not isinstance(self.rule_id, str) or not self.rule_id.strip():
            raise ValueError("rule_id must be a non-empty string")
        if not isinstance(self.enabled, bool):
            raise TypeError("enabled must be a bool")
        # FIA (Mar 2015) §1.1: a system must not place orders where no size limit is
        # set for the instrument. An absent/zero/infinite limit is a mis-configured
        # ruleset, not an unlimited one.
        for name in ("max_order_size", "max_position_size"):
            value = getattr(self, name)
            if not _is_real_number(value) or value <= 0:
                raise ValueError(f"{name} must be a finite number > 0, got {value!r}")
        if not _is_real_number(self.max_daily_loss_usd) or self.max_daily_loss_usd < 0:
            raise ValueError(
                "max_daily_loss_usd must be a finite number >= 0 expressed as a positive "
                f"loss magnitude, got {self.max_daily_loss_usd!r}"
            )
        if not _is_real_number(self.max_price_collar_pct) or self.max_price_collar_pct < 0:
            raise ValueError(
                "max_price_collar_pct must be a finite fraction >= 0 (0.05 == 5%), "
                f"got {self.max_price_collar_pct!r}"
            )


@dataclass
class ProposedOrder:
    """An order presented to the pre-trade gate.

    ``working_buy_quantity``/``working_sell_quantity`` are the unfilled quantities of
    orders already resting on each side. FIA (Mar 2015) §1.2 requires both current
    positions *and* working orders to be evaluated, so that the cap cannot be breached
    by resting orders filling after this one is accepted. They default to ``0.0``, which
    reproduces the naive position-only projection — set them for a faithful test.
    """

    order_id: str
    symbol: str
    side: str  # 'BUY' or 'SELL'
    quantity: float
    price: float
    current_mid_price: float = 100.0
    current_position: float = 0.0
    accumulated_daily_pnl_usd: float = 0.0
    working_buy_quantity: float = 0.0
    working_sell_quantity: float = 0.0


@dataclass
class RiskCheckResult:
    order_id: str
    is_allowed: bool
    triggered_rules: List[str]
    rejection_reasons: List[str]
    latency_microseconds: float


@dataclass(frozen=True)
class RiskTestCase:
    """One expectation about the risk engine's decision for one order.

    ``expected_triggered_rules`` is compared as an **exact set**: a case expecting
    ``POSITION_CAP`` fails if the engine also fires ``MAX_ORDER_SIZE``. An assertion
    that only checks membership cannot detect a spurious extra rejection, which is the
    failure mode that makes an over-tight risk limit invisible in CI.
    """

    test_name: str
    order: ProposedOrder
    expected_allowed: bool
    expected_triggered_rules: Tuple[str, ...] = ()


@dataclass
class TestCaseResult:
    test_name: str
    expected_allowed: bool
    actual_allowed: bool
    expected_triggered_rule: Optional[str]
    passed: bool
    detail: str
    expected_triggered_rules: Tuple[str, ...] = ()
    actual_triggered_rules: Tuple[str, ...] = ()
    latency_microseconds: float = 0.0


@dataclass
class RiskControlTestExecutionReport:
    total_tests: int
    passed_tests: int
    failed_tests: int
    test_results: List[TestCaseResult]
    pass_rate_pct: float
    status: str
    audit_notes: str
    ruleset_id: str = ""
    rules_exercised: Tuple[str, ...] = ()
    required_rule_coverage: Tuple[str, ...] = ()
    missing_rule_coverage: Tuple[str, ...] = ()
    coverage_satisfied: bool = False
    latency_p50_microseconds: float = 0.0
    latency_p99_microseconds: float = 0.0
    latency_sample_count: int = 0
    latency_budget_microseconds: Optional[float] = None
    latency_budget_breached: bool = False
    latency_budget_enforced: bool = False


class PreTradeRiskEngine:
    """Reference pre-trade risk engine exercised by the framework.

    This is a *test fixture*, not a production risk gate: it is single-threaded, holds
    no state across orders, and models neither cumulative controls over a short period
    of time (17 CFR 240.15c3-5(c)(1)(ii)) nor message-rate limits (RTS 6 Art. 15(d)).
    Substitute your own engine — the framework only requires an object exposing
    ``evaluate_order(order) -> RiskCheckResult``.
    """

    def __init__(self, config: RiskRuleConfig) -> None:
        if not isinstance(config, RiskRuleConfig):
            raise TypeError("config must be a RiskRuleConfig")
        self.config = config

    def evaluate_order(self, order: ProposedOrder) -> RiskCheckResult:
        t0 = time.perf_counter_ns()
        cfg = self.config

        if not cfg.enabled:
            # A disabled ruleset allows everything. Loud, because a suite whose
            # positive cases all pass against a disabled ruleset proves nothing.
            logger.warning(
                "Ruleset %s is DISABLED: order %s allowed without evaluation",
                cfg.rule_id, order.order_id,
            )
            return RiskCheckResult(order.order_id, True, [], [], self._elapsed_us(t0))

        triggered: List[str] = []
        reasons: List[str] = []

        invalid_reasons = self._validate_order(order)
        if invalid_reasons:
            # Limits cannot be compared against NaN or a negative quantity, and
            # `NaN > limit` is False for every limit — evaluating on would fail open.
            triggered.append(RULE_INVALID_ORDER)
            reasons.extend(invalid_reasons)
            return self._finish(order, triggered, reasons, t0)

        quantity = float(order.quantity)
        side = order.side.strip().upper()

        # 1. Maximum order size — RTS 6 Art. 15(c); FIA §1.1 "fat-finger" limit.
        if quantity > cfg.max_order_size:
            triggered.append(RULE_MAX_ORDER_SIZE)
            reasons.append(
                f"Quantity {quantity} exceeds max order size {cfg.max_order_size}"
            )

        # 2. Position cap — FIA §1.2: project current position *and* working orders.
        position = float(order.current_position)
        projected_long = position + float(order.working_buy_quantity) + (
            quantity if side == "BUY" else 0.0
        )
        projected_short = position - float(order.working_sell_quantity) - (
            quantity if side == "SELL" else 0.0
        )
        if projected_long > cfg.max_position_size:
            triggered.append(RULE_POSITION_CAP)
            reasons.append(
                f"Worst-case long position {projected_long} exceeds cap "
                f"{cfg.max_position_size} (position {position}, working buys "
                f"{order.working_buy_quantity})"
            )
        elif projected_short < -cfg.max_position_size:
            triggered.append(RULE_POSITION_CAP)
            reasons.append(
                f"Worst-case short position {projected_short} exceeds cap "
                f"-{cfg.max_position_size} (position {position}, working sells "
                f"{order.working_sell_quantity})"
            )

        # 3. Fat-finger price collar — RTS 6 Art. 15(a); FIA §1.4 price tolerance.
        mid = order.current_mid_price
        if not _is_real_number(mid) or float(mid) <= 0:
            # FIA §1.3: unusable reference data blocks the order. Skipping the collar
            # disables it exactly when a stale/absent feed makes a fat finger likeliest.
            triggered.append(RULE_REFERENCE_PRICE_UNAVAILABLE)
            reasons.append(
                f"Reference mid price {mid!r} is unusable; price collar cannot be "
                "evaluated, so the order is blocked"
            )
        else:
            mid_f = float(mid)
            deviation = abs(float(order.price) - mid_f)
            # Multiply rather than divide: `abs(p - m) / m > collar` spuriously rejects
            # an order priced at exactly the collar for ~1% of reference prices
            # (e.g. mid 402.69, price 422.8245 at a 5% collar).
            if deviation > cfg.max_price_collar_pct * mid_f:
                triggered.append(RULE_FAT_FINGER_PRICE_COLLAR)
                reasons.append(
                    f"Price deviation {deviation / mid_f:.4%} exceeds collar "
                    f"{cfg.max_price_collar_pct:.4%} (price {order.price}, mid {mid_f})"
                )

        # 4. Daily loss limit. PnL is signed; the limit is a positive magnitude.
        pnl = float(order.accumulated_daily_pnl_usd)
        if pnl < -cfg.max_daily_loss_usd:
            triggered.append(RULE_DAILY_LOSS_LIMIT)
            reasons.append(
                f"Accumulated daily PnL {pnl} breached loss limit "
                f"-{cfg.max_daily_loss_usd}"
            )

        return self._finish(order, triggered, reasons, t0)

    @staticmethod
    def _validate_order(order: ProposedOrder) -> List[str]:
        """Structural checks whose failure makes every limit comparison meaningless."""
        problems: List[str] = []

        side = order.side.strip().upper() if isinstance(order.side, str) else None
        if side not in _VALID_SIDES:
            # An unrecognised side must not be silently treated as a SELL: that flips
            # the sign of the position projection and hides a position-cap breach.
            problems.append(f"side {order.side!r} is not one of {sorted(_VALID_SIDES)}")

        if not _is_real_number(order.quantity) or float(order.quantity) <= 0:
            problems.append(f"quantity {order.quantity!r} must be a finite number > 0")
        if not _is_real_number(order.price) or float(order.price) <= 0:
            problems.append(f"price {order.price!r} must be a finite number > 0")
        if not _is_real_number(order.current_position):
            problems.append(f"current_position {order.current_position!r} must be finite")
        if not _is_real_number(order.accumulated_daily_pnl_usd):
            problems.append(
                f"accumulated_daily_pnl_usd {order.accumulated_daily_pnl_usd!r} "
                "must be finite"
            )
        for name in ("working_buy_quantity", "working_sell_quantity"):
            value = getattr(order, name)
            if not _is_real_number(value) or float(value) < 0:
                problems.append(f"{name} {value!r} must be a finite number >= 0")

        return problems

    @staticmethod
    def _elapsed_us(t0: int) -> float:
        return (time.perf_counter_ns() - t0) / 1000.0

    def _finish(
        self,
        order: ProposedOrder,
        triggered: List[str],
        reasons: List[str],
        t0: int,
    ) -> RiskCheckResult:
        result = RiskCheckResult(
            order_id=order.order_id,
            is_allowed=not triggered,
            triggered_rules=list(triggered),
            rejection_reasons=list(reasons),
            latency_microseconds=self._elapsed_us(t0),
        )
        if triggered:
            logger.debug(
                "Order %s rejected by %s: %s",
                order.order_id, triggered, "; ".join(reasons),
            )
        return result


class RiskControlUnitTestFrameworkEngine:
    """Executes risk-control test suites and emits an auditable CI/CD gate verdict.

    The verdict is ``report.status``. Branch on it — never on ``failed_tests == 0``,
    which is also true for an empty suite and for a suite that never exercised a rule.
    """

    def __init__(
        self,
        risk_engine: Optional[PreTradeRiskEngine] = None,
        required_rule_coverage: Sequence[str] = DEFAULT_REQUIRED_RULE_COVERAGE,
        latency_budget_microseconds: Optional[float] = None,
        enforce_latency_budget: bool = False,
    ) -> None:
        """
        Args:
            risk_engine: object exposing ``evaluate_order``. Defaults to a
                :class:`PreTradeRiskEngine` on the shipped default limits.
            required_rule_coverage: rule ids a suite must actually trigger. A suite
                that never fires a rule has not tested it, however green it looks.
            latency_budget_microseconds: optional per-evaluation budget, reported as
                p50/p99 over the suite's own evaluations.
            enforce_latency_budget: when True a p99 breach fails the gate. Off by
                default: an interpreted reference engine timed on a shared CI runner
                measures the runner, not the risk gate. See `risk-control-latency-budget`.
        """
        if risk_engine is None:
            risk_engine = PreTradeRiskEngine(
                RiskRuleConfig(
                    rule_id="DEFAULT_RULESET",
                    max_order_size=1000.0,
                    max_position_size=5000.0,
                    max_daily_loss_usd=10000.0,
                    max_price_collar_pct=0.05,
                )
            )
        if not callable(getattr(risk_engine, "evaluate_order", None)):
            raise TypeError("risk_engine must expose a callable evaluate_order(order)")

        coverage = tuple(required_rule_coverage)
        if any(not isinstance(r, str) or not r.strip() for r in coverage):
            raise ValueError("required_rule_coverage entries must be non-empty strings")
        if latency_budget_microseconds is not None:
            if not _is_real_number(latency_budget_microseconds) or latency_budget_microseconds <= 0:
                raise ValueError("latency_budget_microseconds must be a finite number > 0")
        if enforce_latency_budget and latency_budget_microseconds is None:
            raise ValueError(
                "enforce_latency_budget=True requires latency_budget_microseconds"
            )

        self.risk_engine = risk_engine
        self.required_rule_coverage = coverage
        self.latency_budget_microseconds = latency_budget_microseconds
        self.enforce_latency_budget = enforce_latency_budget

    # ------------------------------------------------------------------ cases

    def run_test_case(
        self,
        test_name: str,
        order: ProposedOrder,
        expected_allowed: bool,
        expected_triggered_rule: Optional[str] = None,
        expected_triggered_rules: Optional[Sequence[str]] = None,
    ) -> TestCaseResult:
        """Executes one risk-rule test case and validates the engine's decision.

        Supply either ``expected_triggered_rule`` (single rule) or
        ``expected_triggered_rules`` (exact set). Both are matched exactly.
        """
        case = self._build_case(
            test_name, order, expected_allowed,
            expected_triggered_rule, expected_triggered_rules,
        )
        return self._execute_case(case)

    @staticmethod
    def _build_case(
        test_name: str,
        order: ProposedOrder,
        expected_allowed: bool,
        expected_triggered_rule: Optional[str],
        expected_triggered_rules: Optional[Sequence[str]],
    ) -> RiskTestCase:
        if not isinstance(test_name, str) or not test_name.strip():
            raise ValueError("test_name must be a non-empty string")
        if not isinstance(order, ProposedOrder):
            raise TypeError(f"{test_name}: order must be a ProposedOrder")
        if not isinstance(expected_allowed, bool):
            raise TypeError(f"{test_name}: expected_allowed must be a bool")
        if expected_triggered_rule is not None and expected_triggered_rules is not None:
            raise ValueError(
                f"{test_name}: pass expected_triggered_rule or "
                "expected_triggered_rules, not both"
            )

        if expected_triggered_rules is not None:
            if isinstance(expected_triggered_rules, str):
                raise TypeError(
                    f"{test_name}: expected_triggered_rules must be a sequence of rule "
                    "ids, not a bare string"
                )
            rules = tuple(expected_triggered_rules)
        elif expected_triggered_rule is not None:
            rules = (expected_triggered_rule,)
        else:
            rules = ()

        if any(not isinstance(r, str) or not r.strip() for r in rules):
            raise ValueError(f"{test_name}: rule ids must be non-empty strings")
        if expected_allowed and rules:
            # Self-contradictory: an order cannot be allowed and rejected by a rule.
            raise ValueError(
                f"{test_name}: expected_allowed=True cannot be combined with "
                f"expected rules {list(rules)}"
            )

        return RiskTestCase(
            test_name=test_name,
            order=order,
            expected_allowed=expected_allowed,
            expected_triggered_rules=rules,
        )

    def _execute_case(self, case: RiskTestCase) -> TestCaseResult:
        result = self.risk_engine.evaluate_order(case.order)
        actual_rules = tuple(result.triggered_rules)

        allowed_matches = result.is_allowed == case.expected_allowed
        if case.expected_triggered_rules:
            rules_match = set(actual_rules) == set(case.expected_triggered_rules)
        elif not case.expected_allowed:
            # Rejection expected but no rule named: any rejection reason will do.
            rules_match = bool(actual_rules)
        else:
            rules_match = not actual_rules

        passed = allowed_matches and rules_match
        if passed:
            detail = f"PASSED: allowed={result.is_allowed}, triggered={list(actual_rules)}"
        else:
            detail = (
                f"FAILED: expected allowed={case.expected_allowed}, got "
                f"{result.is_allowed}. Expected rules="
                f"{list(case.expected_triggered_rules)}, got {list(actual_rules)}. "
                f"Reasons: {'; '.join(result.rejection_reasons) or 'none'}"
            )

        legacy_rule = (
            case.expected_triggered_rules[0] if case.expected_triggered_rules else None
        )
        return TestCaseResult(
            test_name=case.test_name,
            expected_allowed=case.expected_allowed,
            actual_allowed=result.is_allowed,
            expected_triggered_rule=legacy_rule,
            passed=passed,
            detail=detail,
            expected_triggered_rules=case.expected_triggered_rules,
            actual_triggered_rules=actual_rules,
            latency_microseconds=result.latency_microseconds,
        )

    # ------------------------------------------------------------------ suites

    def run_suite(self, cases: Sequence[RiskTestCase]) -> RiskControlTestExecutionReport:
        """Executes a suite and returns the CI/CD gate report.

        Raises on a mis-wired suite (blank or duplicate test names, wrong types).
        An empty suite is reported as ``RISK_TEST_SUITE_EMPTY``, never as a pass.
        """
        cases = list(cases)
        seen: Dict[str, int] = {}
        for case in cases:
            if not isinstance(case, RiskTestCase):
                raise TypeError("every suite entry must be a RiskTestCase")
            key = case.test_name.strip()
            if not key:
                raise ValueError("test_name must be a non-empty string")
            if key in seen:
                raise ValueError(f"duplicate test_name {key!r} in suite")
            seen[key] = 1

        results = [self._execute_case(case) for case in cases]
        total = len(results)
        passed_count = sum(1 for r in results if r.passed)
        failed_count = total - passed_count
        pass_rate = round((passed_count / total) * 100.0, 2) if total else 0.0

        exercised = sorted({r for res in results for r in res.actual_triggered_rules})
        missing = tuple(r for r in self.required_rule_coverage if r not in exercised)
        coverage_satisfied = not missing

        latencies = [r.latency_microseconds for r in results]
        p50 = _percentile(latencies, 50.0)
        p99 = _percentile(latencies, 99.0)
        budget = self.latency_budget_microseconds
        budget_breached = bool(budget is not None and total and p99 > budget)

        if total == 0:
            status = STATUS_SUITE_EMPTY
        elif failed_count:
            status = STATUS_FAILURES
        elif not coverage_satisfied:
            status = STATUS_COVERAGE_INCOMPLETE
        elif budget_breached and self.enforce_latency_budget:
            status = STATUS_LATENCY_BREACH
        else:
            status = STATUS_PASSED

        ruleset_id = getattr(getattr(self.risk_engine, "config", None), "rule_id", "UNKNOWN")
        notes = (
            f"RISK TEST SUITE [{status}] ruleset={ruleset_id}: passed {passed_count}/"
            f"{total} ({pass_rate}%); rules exercised {exercised}; missing coverage "
            f"{list(missing)}; latency p50={p50:.2f}us p99={p99:.2f}us"
        )
        if status == STATUS_PASSED:
            logger.info(notes)
        else:
            logger.warning(notes)
        if budget_breached and not self.enforce_latency_budget:
            logger.warning(
                "Latency p99 %.2fus exceeds budget %.2fus (not enforced; timing an "
                "interpreted engine on a shared runner is not a latency benchmark)",
                p99, budget,
            )

        return RiskControlTestExecutionReport(
            total_tests=total,
            passed_tests=passed_count,
            failed_tests=failed_count,
            test_results=results,
            pass_rate_pct=pass_rate,
            status=status,
            audit_notes=notes,
            ruleset_id=ruleset_id,
            rules_exercised=tuple(exercised),
            required_rule_coverage=self.required_rule_coverage,
            missing_rule_coverage=missing,
            coverage_satisfied=coverage_satisfied,
            latency_p50_microseconds=p50,
            latency_p99_microseconds=p99,
            latency_sample_count=len(latencies),
            latency_budget_microseconds=budget,
            latency_budget_breached=budget_breached,
            latency_budget_enforced=self.enforce_latency_budget,
        )

    def build_standard_suite(self) -> List[RiskTestCase]:
        """Standard suite for the shipped default limits.

        Covers, per rule: a valid order, the exact threshold (must pass), a breach just
        past the threshold, and the gross breach. Written against the shipped defaults
        (size 1000, position 5000, daily loss 10000, collar 5%) — rebuild it against
        your own limits rather than assuming these thresholds apply.
        """
        def case(name, order, allowed, rules=()):
            return self._build_case(name, order, allowed, None, tuple(rules))

        return [
            case(
                "Normal Valid Order",
                ProposedOrder("O1", "AAPL", "BUY", 100.0, 150.0, current_mid_price=150.0),
                True,
            ),
            case(
                "Max Order Size At Exact Limit Allowed",
                ProposedOrder("O2", "AAPL", "BUY", 1000.0, 150.0, current_mid_price=150.0),
                True,
            ),
            case(
                "Max Order Size Breach By Smallest Increment",
                ProposedOrder("O3", "AAPL", "BUY", 1000.01, 150.0, current_mid_price=150.0),
                False, [RULE_MAX_ORDER_SIZE],
            ),
            case(
                "Max Order Size Gross Breach",
                ProposedOrder("O4", "AAPL", "BUY", 2000.0, 150.0, current_mid_price=150.0),
                False, [RULE_MAX_ORDER_SIZE],
            ),
            case(
                "Position Cap At Exact Limit Allowed",
                ProposedOrder("O5", "AAPL", "BUY", 1000.0, 150.0,
                              current_mid_price=150.0, current_position=4000.0),
                True,
            ),
            case(
                "Position Cap Long Breach",
                ProposedOrder("O6", "AAPL", "BUY", 900.0, 150.0,
                              current_mid_price=150.0, current_position=4500.0),
                False, [RULE_POSITION_CAP],
            ),
            case(
                "Position Cap Short Breach",
                ProposedOrder("O7", "AAPL", "SELL", 900.0, 150.0,
                              current_mid_price=150.0, current_position=-4500.0),
                False, [RULE_POSITION_CAP],
            ),
            case(
                "Position Cap Includes Working Orders",
                ProposedOrder("O8", "AAPL", "BUY", 200.0, 150.0,
                              current_mid_price=150.0, current_position=4000.0,
                              working_buy_quantity=900.0),
                False, [RULE_POSITION_CAP],
            ),
            case(
                "Price Collar At Exact Limit Allowed",
                ProposedOrder("O9", "AAPL", "BUY", 100.0, 422.8245,
                              current_mid_price=402.69),
                True,
            ),
            case(
                "Price Collar Gross Breach",
                ProposedOrder("O10", "AAPL", "BUY", 100.0, 200.0, current_mid_price=150.0),
                False, [RULE_FAT_FINGER_PRICE_COLLAR],
            ),
            case(
                "Reference Price Unavailable Blocks Order",
                ProposedOrder("O11", "AAPL", "BUY", 100.0, 150.0, current_mid_price=0.0),
                False, [RULE_REFERENCE_PRICE_UNAVAILABLE],
            ),
            case(
                "Daily Loss At Exact Limit Allowed",
                ProposedOrder("O12", "AAPL", "BUY", 100.0, 150.0, current_mid_price=150.0,
                              accumulated_daily_pnl_usd=-10000.0),
                True,
            ),
            case(
                "Daily Loss Limit Breach",
                ProposedOrder("O13", "AAPL", "BUY", 100.0, 150.0, current_mid_price=150.0,
                              accumulated_daily_pnl_usd=-15000.0),
                False, [RULE_DAILY_LOSS_LIMIT],
            ),
            case(
                "Invalid Order NaN Quantity",
                ProposedOrder("O14", "AAPL", "BUY", float("nan"), 150.0,
                              current_mid_price=150.0),
                False, [RULE_INVALID_ORDER],
            ),
            case(
                "Invalid Order Negative Quantity",
                ProposedOrder("O15", "AAPL", "BUY", -100.0, 150.0, current_mid_price=150.0),
                False, [RULE_INVALID_ORDER],
            ),
            case(
                "Invalid Order Unrecognised Side",
                ProposedOrder("O16", "AAPL", "BUYY", 100.0, 150.0, current_mid_price=150.0),
                False, [RULE_INVALID_ORDER],
            ),
        ]

    def run_standard_suite(self) -> RiskControlTestExecutionReport:
        """Executes the standard pre-trade risk rule suite."""
        return self.run_suite(self.build_standard_suite())
