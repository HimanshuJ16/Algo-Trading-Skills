#!/usr/bin/env python3
"""
Example 02: Lookahead-Free Backtest with Realistic Slippage & Tearsheet

Chains 3 Skills:
  1. lookahead-bias-elimination (Point-in-time signal calculations using bar close offset)
  2. realistic-slippage-fee-latency-simulation (Execution modeling with latency & market impact)
  3. standardized-tearsheet-generation (Institutional summary metrics: Sharpe, Drawdown, CAGR)
"""
import math
import random
from typing import List, Dict, Any


def generate_mock_bar_data(num_bars: int = 100) -> List[Dict[str, float]]:
    price = 100.0
    bars = []
    for i in range(num_bars):
        change = random.gauss(0.0005, 0.01)
        open_p = price
        close_p = price * (1 + change)
        high_p = max(open_p, close_p) * (1 + random.uniform(0.001, 0.005))
        low_p = min(open_p, close_p) * (1 - random.uniform(0.001, 0.005))
        bars.append({"timestamp": i, "open": open_p, "high": high_p, "low": low_p, "close": close_p, "volume": random.randint(1000, 5000)})
        price = close_p
    return bars


class LookaheadFreeSignalEngine:
    """Skill: lookahead-bias-elimination"""

    def compute_signal(self, historical_bars: List[Dict[str, float]]) -> float:
        """
        Calculates moving average crossover using STRICTLY completed historical bars.
        Never references current bar's close price until the bar period ends.
        """
        if len(historical_bars) < 20:
            return 0.0
        # Use completed bars up to index -1
        short_ma = sum(b["close"] for b in historical_bars[-5:]) / 5.0
        long_ma = sum(b["close"] for b in historical_bars[-20:]) / 20.0
        return 1.0 if short_ma > long_ma else -1.0


class RealisticExecutionSimulator:
    """Skill: realistic-slippage-fee-latency-simulation"""

    def __init__(self, fee_bps: float = 5.0, latency_bars: int = 1):
        self.fee_bps = fee_bps
        self.latency_bars = latency_bars

    def execute_trade(self, signal: float, execution_bar: Dict[str, float], qty: float) -> Dict[str, float]:
        base_price = execution_bar["open"]
        # Quadratic market impact slippage simulation
        slippage_pct = 0.0002 * math.sqrt(abs(qty) / 100.0)
        filled_price = base_price * (1 + slippage_pct) if signal > 0 else base_price * (1 - slippage_pct)
        fee = filled_price * qty * (self.fee_bps / 10000.0)
        return {"filled_price": filled_price, "fee": fee}


class StandardizedTearsheet:
    """Skill: standardized-tearsheet-generation"""

    def generate(self, returns: List[float]):
        if not returns:
            return
        avg_ret = sum(returns) / len(returns)
        std_ret = math.sqrt(sum((r - avg_ret) ** 2 for r in returns) / len(returns)) if len(returns) > 1 else 1e-6
        sharpe = (avg_ret / std_ret) * math.sqrt(252) if std_ret > 0 else 0.0
        cum_equity = [1.0]
        for r in returns:
            cum_equity.append(cum_equity[-1] * (1 + r))
        max_dd = 0.0
        peak = cum_equity[0]
        for e in cum_equity:
            if e > peak:
                peak = e
            dd = (peak - e) / peak
            if dd > max_dd:
                max_dd = dd

        print("\n--- Institutional Strategy Performance Tearsheet ---")
        print(f"Total Trades Executed : {len(returns)}")
        print(f"Mean Period Return    : {avg_ret * 100:.4f}%")
        print(f"Annualized Sharpe     : {sharpe:.2f}")
        print(f"Max Peak-to-Trough DD : {max_dd * 100:.2f}%")
        print("---------------------------------------------------\n")


def main():
    print("=== Walkthrough 02: Lookahead-Free Backtest & Execution Simulation ===\n")
    bars = generate_mock_bar_data(120)
    engine = LookaheadFreeSignalEngine()
    simulator = RealisticExecutionSimulator(fee_bps=5.0, latency_bars=1)
    tearsheet = StandardizedTearsheet()

    returns = []
    position = 0.0

    for i in range(20, len(bars) - 1):
        # Step 1: Compute signal on COMPLETED historical bars (0..i-1)
        history = bars[:i]
        signal = engine.compute_signal(history)

        # Step 2: Execute trade on NEXT bar's open (simulating 1-bar latency & slippage)
        if signal != position:
            exec_bar = bars[i]  # Next bar
            fill = simulator.execute_trade(signal, exec_bar, qty=100.0)
            trade_return = (bars[i+1]["close"] - fill["filled_price"]) / fill["filled_price"] if signal > 0 else (fill["filled_price"] - bars[i+1]["close"]) / fill["filled_price"]
            returns.append(trade_return)
            position = signal

    # Step 3: Generate performance tearsheet
    tearsheet.generate(returns)
    print("=== Walkthrough 02 Completed Cleanly ===")


if __name__ == "__main__":
    main()
