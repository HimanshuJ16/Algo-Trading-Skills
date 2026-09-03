#!/usr/bin/env python3
"""
Example 03: Cross-Strategy Correlation, Risk Parity & Strategy Retirement

Runs the real helper modules from three skills — nothing here re-implements
them:

  1. cross-strategy-correlation-monitoring
     (`cross_strategy_correlation_monitoring.CrossStrategyCorrelationMonitor`)
     Pairwise PnL correlations, the Choueifaty-Coignard diversification ratio,
     and named redundancy breaches.
  2. risk-parity-allocation-across-strategies
     (`risk_parity_allocation_across_strategies.RiskParityAllocationEngine`)
     Equal-risk-contribution weights solved against the covariance the monitor
     already estimated, then audited for how close to parity they land.
  3. strategy-lifecycle-retirement-criteria
     (`strategy_lifecycle_retirement_criteria.StrategyLifecycleRetirementEngine`)
     A pre-declared retirement rule applied identically to every strategy.

The three compose: the monitor's covariance feeds the allocator, and a strategy
the lifecycle engine retires drops out of the next allocation.

Run from the repository root:

    python examples/03_cross_strategy_risk_parity_allocation.py
"""
import logging
import os
import sys
from typing import Dict, List

import numpy as np

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _slug in (
    "cross-strategy-correlation-monitoring",
    "risk-parity-allocation-across-strategies",
    "strategy-lifecycle-retirement-criteria",
):
    sys.path.insert(0, os.path.join(REPO_ROOT, "skills", _slug, "scripts"))

from cross_strategy_correlation_monitoring import (  # noqa: E402
    CrossStrategyCorrelationMonitor,
)
from risk_parity_allocation_across_strategies import (  # noqa: E402
    AllocationMethod,
    RiskParityAllocationEngine,
    StrategyRiskData,
)
from strategy_lifecycle_retirement_criteria import (  # noqa: E402
    StrategyLifecycleRetirementEngine,
    StrategyPerformanceMetrics,
)

# Seeded so the correlations, weights and decisions below are the same on every
# run; nothing here should move because a random draw moved.
RNG = np.random.default_rng(42)

# The helper modules log through the standard library. Surface their warnings,
# prefixed so they are visibly theirs and not this script's narration.
logging.basicConfig(level=logging.WARNING, format="  [%(name)s] %(message)s")

TRADING_DAYS = 252
OBSERVATIONS = 180
TOTAL_CAPITAL = 5_000_000.0

STRATEGIES = ["StatArb_US", "StatArb_EU", "FX_Carry"]


def generate_pnl_matrix() -> np.ndarray:
    """Daily returns for three pods, with a deliberate redundancy built in.

    `StatArb_EU` is constructed to be ~0.9 correlated with `StatArb_US` so the
    monitor has a genuine breach to find; `FX_Carry` is independent and drifts
    down, so the lifecycle engine has a genuine decay to adjudicate.
    """
    z = RNG.normal(size=(OBSERVATIONS, 3))
    us = 0.0004 + 0.008 * z[:, 0]
    eu = 0.0003 + 0.009 * (0.9 * z[:, 0] + np.sqrt(1.0 - 0.81) * z[:, 1])
    fx = -0.0006 + 0.012 * z[:, 2]
    return np.column_stack([us, eu, fx])


def live_stats(returns: np.ndarray) -> Dict[str, float]:
    """Annualized return and max drawdown (as a positive magnitude) for one pod."""
    equity = np.concatenate(([1.0], np.cumprod(1.0 + returns)))
    peak = np.maximum.accumulate(equity)
    max_dd_pct = float(np.max((peak - equity) / peak) * 100.0)
    annual_return_pct = float(np.mean(returns) * TRADING_DAYS * 100.0)
    sharpe = float(np.mean(returns) / np.std(returns, ddof=1) * np.sqrt(TRADING_DAYS))
    return {
        "annual_return_pct": annual_return_pct,
        "max_drawdown_pct": max_dd_pct,
        "sharpe": sharpe,
    }


def print_allocation(report, title: str) -> None:
    print(title)
    print("  method=%s  covariance supplied=%s  solver sweeps=%d"
          % (report.method, report.covariance_supplied, report.solver_iterations))
    for allocation in report.allocations:
        print("    %-12s weight %6.2f%%  capital %13s  vol %5.2f%%  "
              "risk share %6.2f%% (target %.2f%%)"
              % (allocation.strategy_id,
                 100.0 * allocation.weight,
                 "{:,.0f}".format(allocation.allocated_capital_usd),
                 100.0 * allocation.annualized_volatility,
                 allocation.risk_contribution_pct,
                 allocation.target_risk_contribution_pct))
    print("  portfolio vol %.2f%%, max risk-parity error %.2f pp -> %s"
          % (report.portfolio_annualized_volatility,
             report.max_risk_parity_error_pct, report.status))


def main() -> None:
    print("=== Walkthrough 03: Correlation, Risk Parity & Strategy Retirement ===\n")

    pnl = generate_pnl_matrix()

    # --- Step 1: is this portfolio actually diversified? -------------------
    monitor = CrossStrategyCorrelationMonitor(
        high_correlation_threshold=0.70,
        redundancy_threshold=0.85,
        min_diversification_ratio=1.20,
        min_observations=30,
        shrinkage_delta=0.0,   # so shrunk_covariance_matrix is the raw estimate
    )
    report = monitor.analyze_strategy_correlations(
        strategy_names=STRATEGIES,
        pnl_returns_matrix=pnl,
        weights=[1.0 / len(STRATEGIES)] * len(STRATEGIES),
    )

    print("Correlation matrix (%d observations, effective %.1f):"
          % (report.observations_used, report.effective_observations))
    print("               " + "".join("%12s" % name for name in STRATEGIES))
    for i, name in enumerate(STRATEGIES):
        row = "".join("%12.3f" % report.correlation_matrix[i, j]
                      for j in range(len(STRATEGIES)))
        print("  %-12s%s" % (name, row))
    print("  diversification ratio %.3f (floor %.2f), average off-diagonal "
          "correlation %.3f"
          % (report.diversification_ratio, monitor.min_diversification_ratio,
             report.average_inter_strategy_correlation))
    print("  healthy: %s" % report.is_diversification_healthy)
    for breach in report.high_correlation_breaches:
        print("  [%s] %s / %s at %.2f"
              % (breach.severity, breach.strategy_a, breach.strategy_b,
                 breach.correlation))
    for recommendation in report.recommendations:
        print("  -> %s" % recommendation)
    print()

    # --- Step 2: size by equal risk contribution, on that same covariance ---
    # The monitor already estimated the daily covariance; annualize it rather
    # than estimating a second, subtly different one.
    covariance_annual = np.asarray(report.shrunk_covariance_matrix) * TRADING_DAYS
    vols = np.sqrt(np.diag(covariance_annual))

    allocator = RiskParityAllocationEngine()
    risk_data: List[StrategyRiskData] = [
        StrategyRiskData(strategy_id=name, annualized_volatility=float(vols[i]))
        for i, name in enumerate(STRATEGIES)
    ]
    allocation_report = allocator.compute_risk_parity_allocation(
        strategies=risk_data,
        total_capital_usd=TOTAL_CAPITAL,
        covariance_matrix=covariance_annual.tolist(),
        method=AllocationMethod.EQUAL_RISK_CONTRIBUTION,
    )
    print_allocation(allocation_report, "Equal-risk-contribution allocation:")
    print("  Inverse volatility alone would have given "
          + ", ".join("%s %.2f%%" % (STRATEGIES[i], 100.0 * w)
                      for i, w in enumerate(
                          allocator.compute_inverse_vol_weights(risk_data)))
          + ": the correlation structure is what moves them apart.\n")

    # --- Step 3: adjudicate each strategy against a pre-declared rule ------
    # Backtest figures and the IC t-stat come from the research record; every
    # live figure is measured from the returns above. Against a zero benchmark
    # and a zero risk-free rate the live information ratio is the live Sharpe,
    # so it is passed through rather than declared separately.
    research_record = {
        "StatArb_US": {"backtest_sharpe": 1.90, "backtest_max_drawdown_pct": 8.0,
                       "backtest_annual_return_pct": 14.0, "live_ic_t_stat": 3.10},
        "StatArb_EU": {"backtest_sharpe": 1.40, "backtest_max_drawdown_pct": 10.0,
                       "backtest_annual_return_pct": 11.0, "live_ic_t_stat": 2.20},
        "FX_Carry":   {"backtest_sharpe": 1.20, "backtest_max_drawdown_pct": 9.0,
                       "backtest_annual_return_pct": 12.0, "live_ic_t_stat": 0.40},
    }

    lifecycle = StrategyLifecycleRetirementEngine(min_live_observations=120)
    survivors: List[int] = []
    print("Lifecycle adjudication:")
    for i, name in enumerate(STRATEGIES):
        live = live_stats(pnl[:, i])
        record = research_record[name]
        decision_report = lifecycle.evaluate_strategy(StrategyPerformanceMetrics(
            strategy_id=name,
            backtest_sharpe=record["backtest_sharpe"],
            backtest_max_drawdown_pct=record["backtest_max_drawdown_pct"],
            live_sharpe=live["sharpe"],
            live_max_drawdown_pct=live["max_drawdown_pct"],
            live_information_ratio=live["sharpe"],
            live_ic_t_stat=record["live_ic_t_stat"],
            live_realized_annual_return_pct=live["annual_return_pct"],
            backtest_annual_return_pct=record["backtest_annual_return_pct"],
            live_observation_count=OBSERVATIONS,
        ))
        print("  %-12s %-22s live Sharpe %5.2f, live max DD %5.1f%%, drift %s"
              % (name, decision_report.decision.value, live["sharpe"],
                 live["max_drawdown_pct"],
                 "not measurable" if decision_report.performance_drift_pct is None
                 else "%.1f%%" % decision_report.performance_drift_pct))
        for criterion in decision_report.breached_criteria:
            print("      - %s" % criterion)
        if decision_report.skipped_criteria:
            print("      (skipped: %s)"
                  % "; ".join(decision_report.skipped_criteria))
        if decision_report.is_retired:
            print("      [ACTION] %s" % decision_report.recommended_action)
        else:
            survivors.append(i)
    print()

    # --- Step 4: reallocate what is left -----------------------------------
    if len(survivors) == len(STRATEGIES):
        print("No strategy was retired; the allocation above stands.")
    elif len(survivors) < 2:
        print("Fewer than two strategies survive; risk parity across one book is "
              "not a portfolio decision. Escalate to the strategy committee.")
    else:
        sub_cov = covariance_annual[np.ix_(survivors, survivors)]
        sub_data = [
            StrategyRiskData(strategy_id=STRATEGIES[i],
                             annualized_volatility=float(vols[i]))
            for i in survivors
        ]
        reallocated = allocator.compute_risk_parity_allocation(
            strategies=sub_data,
            total_capital_usd=TOTAL_CAPITAL,
            covariance_matrix=sub_cov.tolist(),
            method=AllocationMethod.EQUAL_RISK_CONTRIBUTION,
        )
        print_allocation(
            reallocated,
            "Reallocation after retirement (%s removed):"
            % ", ".join(STRATEGIES[i] for i in range(len(STRATEGIES))
                        if i not in survivors),
        )
        print("  Note the surviving pair is the redundant one the monitor "
              "flagged: retiring a decayed pod can concentrate the book, so "
              "read step 1 again before signing this off.")

    print("\n=== Walkthrough 03 Completed Cleanly ===")


if __name__ == "__main__":
    main()
