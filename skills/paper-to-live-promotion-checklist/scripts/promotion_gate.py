"""
paper-to-live-promotion-checklist: Production-grade paper-to-live promotion gate,
multi-criteria quantitative validator, human sign-off report generator,
and post-promotion live rollback trigger evaluator.
"""
from dataclasses import dataclass, field
import datetime
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class GateCheckResult:
    check_name: str
    passed: bool
    observed_value: Any
    expected_value: Any
    details: str


@dataclass
class PromotionDecisionReport:
    approved: bool
    checks: List[GateCheckResult]
    summary: str
    reviewer_id: Optional[str] = None
    initial_live_sizing_pct: float = 0.25  # Default start live at 25% target sizing
    rollback_drawdown_pct: float = 0.05    # Rollback to paper if live drawdown >= 5%


class PaperToLivePromotionGate:
    """
    Automated promotion gate evaluating paper-trading logs against backtest expectations
    before authorizing promotion to live capital.
    """

    def __init__(
        self,
        min_days: int = 20,
        slippage_tolerance_pct: float = 0.15,
        accuracy_tolerance_pct: float = 0.10,
        min_trades_count: int = 30,
    ):
        self.min_days = min_days
        self.slippage_tolerance_pct = slippage_tolerance_pct
        self.accuracy_tolerance_pct = accuracy_tolerance_pct
        self.min_trades_count = min_trades_count

    def evaluate_gate(
        self, paper_stats: Dict[str, Any], backtest_stats: Dict[str, Any]
    ) -> PromotionDecisionReport:

        checks = []

        # 1. Minimum Paper Duration Check
        days_run = paper_stats.get("days_run", 0)
        pass_days = days_run >= self.min_days
        checks.append(
            GateCheckResult(
                check_name="min_paper_duration",
                passed=pass_days,
                observed_value=f"{days_run} days",
                expected_value=f">= {self.min_days} days",
                details=f"Paper trading ran for {days_run} days.",
            )
        )

        # 2. Minimum Trades Count Check
        trades_count = paper_stats.get("trades_count", 0)
        pass_trades = trades_count >= self.min_trades_count
        checks.append(
            GateCheckResult(
                check_name="min_trades_count",
                passed=pass_trades,
                observed_value=trades_count,
                expected_value=f">= {self.min_trades_count}",
                details=f"Paper trading recorded {trades_count} trades.",
            )
        )

        # 3. Slippage Alignment Check
        paper_slip = paper_stats.get("avg_slippage", 0.0)
        bt_slip = backtest_stats.get("modeled_slippage", 0.001)
        slip_diff = abs(paper_slip - bt_slip) / bt_slip if bt_slip > 0 else 0.0
        pass_slip = slip_diff <= self.slippage_tolerance_pct
        checks.append(
            GateCheckResult(
                check_name="slippage_alignment",
                passed=pass_slip,
                observed_value=f"{paper_slip:.4f}",
                expected_value=f"{bt_slip:.4f} (+/- {self.slippage_tolerance_pct:.0%})",
                details=f"Slippage difference {slip_diff:.1%} vs backtest expectation.",
            )
        )

        # 4. Signal Accuracy Alignment Check
        paper_acc = paper_stats.get("signal_accuracy", 0.0)
        bt_acc = backtest_stats.get("walk_forward_accuracy", 0.50)
        acc_diff = abs(paper_acc - bt_acc)
        pass_acc = acc_diff <= self.accuracy_tolerance_pct
        checks.append(
            GateCheckResult(
                check_name="accuracy_alignment",
                passed=pass_acc,
                observed_value=f"{paper_acc:.2%}",
                expected_value=f"{bt_acc:.2%} (+/- {self.accuracy_tolerance_pct:.0%})",
                details=f"Signal accuracy difference {acc_diff:.2%} vs walk-forward accuracy.",
            )
        )

        # 5. Risk Controls Exercised Check
        risk_triggers = paper_stats.get("risk_controls_triggered", 0)
        pass_risk = risk_triggers > 0
        checks.append(
            GateCheckResult(
                check_name="risk_controls_exercised",
                passed=pass_risk,
                observed_value=risk_triggers,
                expected_value=">= 1",
                details=f"Risk controls triggered {risk_triggers} times in paper mode.",
            )
        )

        # 6. Auth Token Expiry Cycle Survived
        reauth_cycles = paper_stats.get("reauth_cycles_survived", 0)
        pass_reauth = reauth_cycles >= 1
        checks.append(
            GateCheckResult(
                check_name="auth_reauth_survived",
                passed=pass_reauth,
                observed_value=reauth_cycles,
                expected_value=">= 1",
                details=f"Survived {reauth_cycles} natural/forced token expiry cycles.",
            )
        )

        all_passed = all(c.passed for c in checks)
        summary = (
            "PROMOTION APPROVED: All paper-to-live gate checks passed successfully."
            if all_passed
            else "PROMOTION REJECTED: One or more gate criteria failed. Review details."
        )

        return PromotionDecisionReport(approved=all_passed, checks=checks, summary=summary)

    def check_rollback_trigger(
        self, live_stats: Dict[str, Any], paper_baseline: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """
        Evaluates early live trading performance; triggers rollback to paper mode if live drawdown or
        slippage exceeds 2x paper baseline.
        """
        live_dd = live_stats.get("max_drawdown_pct", 0.0)
        paper_dd = paper_baseline.get("max_drawdown_pct", 0.02)
        live_slip = live_stats.get("avg_slippage", 0.0)
        paper_slip = paper_baseline.get("avg_slippage", 0.001)

        if live_dd >= max(0.05, paper_dd * 2.0):
            msg = f"ROLLBACK TRIGGERED: Live drawdown {live_dd:.2%} breached threshold ({paper_dd*2.0:.2%})."
            logger.critical(msg)
            return True, msg

        if live_slip >= max(0.005, paper_slip * 2.0):
            msg = f"ROLLBACK TRIGGERED: Live slippage {live_slip:.4f} breached 2x paper baseline ({paper_slip*2.0:.4f})."
            logger.critical(msg)
            return True, msg

        return False, "Live metrics operating within acceptable bounds."


# Backward compatibility functions
def evaluate_promotion_gate(paper_stats, backtest_stats, min_days=20, tolerance=0.15):
    checks = {}
    checks["min_duration_met"] = paper_stats.get("days_run", 0) >= min_days

    paper_slip = paper_stats.get("avg_slippage", 0.0)
    bt_slip = backtest_stats.get("modeled_slippage", 0.001)
    checks["slippage_within_tolerance"] = abs(paper_slip - bt_slip) <= tolerance * bt_slip

    paper_acc = paper_stats.get("signal_accuracy", 0.0)
    bt_acc = backtest_stats.get("walk_forward_accuracy", 0.50)
    checks["accuracy_within_tolerance"] = abs(paper_acc - bt_acc) <= tolerance

    checks["risk_controls_exercised"] = paper_stats.get("risk_controls_triggered", 0) > 0
    checks["all_pass"] = all(v for k, v in checks.items() if k != "all_pass")
    return checks
