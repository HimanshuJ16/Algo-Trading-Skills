#!/usr/bin/env python3
"""
Example 03: Cross-Strategy Correlation, Risk Parity & Strategy Retirement

Chains 3 Skills:
  1. cross-strategy-correlation-monitoring (Compute rolling correlation matrix across sub-strategies)
  2. risk-parity-allocation (Allocate portfolio capital inverse to strategy risk/volatility)
  3. strategy-lifecycle-retirement-criteria (Decommission degraded strategies automatically)
"""
import math
from typing import Dict, List


class CrossStrategyCorrelationMonitor:
    """Skill: cross-strategy-correlation-monitoring"""

    def compute_correlation(self, returns_a: List[float], returns_b: List[float]) -> float:
        n = len(returns_a)
        mean_a = sum(returns_a) / n
        mean_b = sum(returns_b) / n
        cov = sum((returns_a[i] - mean_a) * (returns_b[i] - mean_b) for i in range(n))
        var_a = sum((r - mean_a) ** 2 for r in returns_a)
        var_b = sum((r - mean_b) ** 2 for r in returns_b)
        denom = math.sqrt(var_a * var_b)
        return cov / denom if denom > 0 else 0.0


class RiskParityAllocator:
    """Skill: risk-parity-allocation"""

    def allocate(self, volatilities: Dict[str, float]) -> Dict[str, float]:
        inv_vols = {s: 1.0 / v for s, v in volatilities.items() if v > 0}
        total_inv = sum(inv_vols.values())
        return {s: inv_v / total_inv for s, inv_v in inv_vols.items()}


class StrategyLifecycleManager:
    """Skill: strategy-lifecycle-retirement-criteria"""

    def evaluate_health(self, strategy_id: str, sharpe_ratio: float, max_drawdown: float) -> str:
        if sharpe_ratio < 0.2 or max_drawdown > 0.25:
            return "RETIRE"
        elif sharpe_ratio < 0.8:
            return "WARNING"
        return "HEALTHY"


def main():
    print("=== Walkthrough 03: Multi-Strategy Portfolio Risk Parity & Health Monitoring ===\n")

    strat_returns = {
        "StatArb_US": [0.01, -0.005, 0.012, 0.008, -0.002, 0.015, 0.003],
        "Trend_Crypto": [0.03, -0.025, 0.04, -0.035, 0.02, 0.05, -0.04],
        "FX_Carry_Degraded": [-0.01, -0.015, -0.008, -0.02, -0.012, -0.005, -0.018]
    }

    # Step 1: Monitor rolling correlations
    corr_monitor = CrossStrategyCorrelationMonitor()
    corr = corr_monitor.compute_correlation(strat_returns["StatArb_US"], strat_returns["Trend_Crypto"])
    print(f"StatArb vs Trend Crypto Correlation: {corr:.3f}")

    # Step 2: Risk Parity Allocation based on strategy volatility
    volatilities = {}
    for strat, rets in strat_returns.items():
        mean_r = sum(rets) / len(rets)
        vol = math.sqrt(sum((r - mean_r) ** 2 for r in rets) / len(rets))
        volatilities[strat] = vol

    allocator = RiskParityAllocator()
    weights = allocator.allocate(volatilities)
    print("\nRisk Parity Portfolio Weights:")
    for strat, w in weights.items():
        print(f"  {strat:20s}: {w * 100:.2f}% (Vol: {volatilities[strat]*100:.2f}%)")

    # Step 3: Lifecycle Health Assessment & Retirement
    lifecycle = StrategyLifecycleManager()
    print("\nStrategy Health Evaluation:")
    for strat in strat_returns.keys():
        # Simulated metrics for health evaluation
        sharpe = 1.8 if "StatArb" in strat else (1.1 if "Trend" in strat else -0.4)
        dd = 0.04 if "StatArb" in strat else (0.12 if "Trend" in strat else 0.32)
        status = lifecycle.evaluate_health(strat, sharpe, dd)
        print(f"  {strat:20s} -> Sharpe: {sharpe:4.1f}, Max DD: {dd*100:4.1f}% | Status: {status}")
        if status == "RETIRE":
            print(f"    [ACTION] Decommissioning strategy {strat} and reallocating capital.")

    print("\n=== Walkthrough 03 Completed Cleanly ===")


if __name__ == "__main__":
    main()
