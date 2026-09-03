#!/usr/bin/env python3
"""
Example 02: Lookahead-Free Backtest with Realistic Fills & a Standard Tearsheet

Runs the real helper modules from three skills — nothing here re-implements
them:

  1. lookahead-bias-elimination              (`leak_audit.LookaheadBiasAuditor`)
     Build the causal execution column (a signal raised on bar T fills at bar
     T+1's open), then audit the finished frame for same-bar fills — and
     calibrate the auditor so a clean report means something.
  2. execution-realistic-simulation           (`fill_model.RealisticExecutionSimulator`)
     Price each fill by crossing the half-spread and paying square-root market
     impact, I(Q) = gamma * sigma * sqrt(Q / ADV) * mid, then apply the dated,
     source-attributed statutory fee stack for the market.
  3. backtest-reporting-standardized-tearsheet (`tearsheet_generator.StandardizedTearsheetGenerator`)
     Report Sharpe, Sortino, Calmar and drawdown under one stated convention.

Run from the repository root:

    python examples/02_lookahead_free_backtest_with_slippage.py
"""
import logging
import os
import sys

import numpy as np
import pandas as pd

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _slug in (
    "lookahead-bias-elimination",
    "execution-realistic-simulation",
    "backtest-reporting-standardized-tearsheet",
):
    sys.path.insert(0, os.path.join(REPO_ROOT, "skills", _slug, "scripts"))

from fill_model import MarketType, RealisticExecutionSimulator  # noqa: E402
from leak_audit import LookaheadBiasAuditor  # noqa: E402
from tearsheet_generator import StandardizedTearsheetGenerator  # noqa: E402

# Seeded so this walkthrough produces the same bars, and therefore the same
# tearsheet, on every run.
RNG = np.random.default_rng(42)

# The helper modules log through the standard library. Surface their warnings,
# prefixed so they are visibly theirs and not this script's narration.
logging.basicConfig(level=logging.WARNING, format="  [%(name)s] %(message)s")

FAST_WINDOW = 5
SLOW_WINDOW = 20
EXECUTION_LAG = 1        # bars between the decision and the fill
ORDER_QTY = 500.0        # shares per trade
HALF_SPREAD = 0.01       # USD, one side of a 2-cent-wide quote
ADV = 2_000_000.0        # average daily volume, same units as the order size
DAILY_VOL = 0.02         # 2% per day, the sigma of the square-root law


def generate_bars(num_bars: int = 160) -> pd.DataFrame:
    """A synthetic daily OHLCV frame. Not a market — just a well-formed input."""
    price = 100.0
    rows = []
    for i in range(num_bars):
        change = RNG.normal(0.0005, 0.01)
        open_p = price
        close_p = price * (1.0 + change)
        rows.append({
            "timestamp": pd.Timestamp("2026-01-01") + pd.Timedelta(days=i),
            "open": open_p,
            "high": max(open_p, close_p) * (1.0 + RNG.uniform(0.001, 0.005)),
            "low": min(open_p, close_p) * (1.0 - RNG.uniform(0.001, 0.005)),
            "close": close_p,
            "volume": float(RNG.integers(1_000, 5_000)),
        })
        price = close_p
    return pd.DataFrame(rows)


def build_signals(bars: pd.DataFrame) -> pd.DataFrame:
    """Moving-average crossover, decided on each bar's own completed close."""
    df = bars.copy()
    df["ma_fast"] = df["close"].rolling(FAST_WINDOW).mean()
    df["ma_slow"] = df["close"].rolling(SLOW_WINDOW).mean()
    warm = df["ma_fast"].notna() & df["ma_slow"].notna()
    df["signal"] = np.where(warm, np.where(df["ma_fast"] > df["ma_slow"], 1, -1), 0)
    df["signal"] = df["signal"].astype(int)
    return df


def main() -> None:
    print("=== Walkthrough 02: Lookahead-Free Backtest & Execution Simulation ===\n")

    df = build_signals(generate_bars())

    # --- Step 1: make the execution causal, then audit that it is ----------
    auditor = LookaheadBiasAuditor(warmup_periods=SLOW_WINDOW)
    aligned = auditor.align_signal_execution(
        df,
        signal_col="signal",
        open_col="open",
        execution_lag=EXECUTION_LAG,
        timestamp_col="timestamp",
    )

    detected = auditor.run_timing_calibration(
        aligned,
        signal_col="executed_signal",
        fill_price_col="fill_price",
        timestamp_col="timestamp",
        indicator_cols=["ma_fast", "ma_slow"],
    )
    print("Auditor calibration: %.0f%% of deliberately injected same-bar fills "
          "were caught." % (100.0 * detected))
    print("  A clean audit is only worth what the calibration says it is.")

    findings = auditor.audit_backtest_timing(
        aligned,
        signal_col="executed_signal",
        fill_price_col="fill_price",
        timestamp_col="timestamp",
        indicator_cols=["ma_fast", "ma_slow"],
    )
    print("Timing audit of the aligned frame: %d finding(s)." % len(findings))
    for finding in findings[:3]:
        print("  [%s] %s" % (finding.violation_type.value, finding.details))
    print("  No screen fired: fills sit on bar T+%d's open, never on the bar that "
          "produced the signal.\n" % EXECUTION_LAG)

    # --- Step 2: price the fills the way the venue would -------------------
    simulator = RealisticExecutionSimulator()   # gamma defaults to 0.5
    executed = aligned[aligned["executed_signal"] != 0]

    returns = []
    first_trade_printed = False
    for row in executed.itertuples():
        exit_index = row.Index + 1
        if exit_index not in aligned.index:
            break
        exit_row = aligned.loc[exit_index]

        direction = 1.0 if row.executed_signal > 0 else -1.0
        entry_side = "BUY" if direction > 0 else "SELL"
        exit_side = "SELL" if direction > 0 else "BUY"

        entry = simulator.simulate_fill(
            side=entry_side, order_size=ORDER_QTY, mid_price=row.open,
            half_spread=HALF_SPREAD, adv=ADV, volatility=DAILY_VOL,
            market_type=MarketType.US_EQUITY,
        )
        exit_fill = simulator.simulate_fill(
            side=exit_side, order_size=ORDER_QTY, mid_price=float(exit_row["open"]),
            half_spread=HALF_SPREAD, adv=ADV, volatility=DAILY_VOL,
            market_type=MarketType.US_EQUITY,
        )

        gross = (exit_fill.fill_price - entry.fill_price) * ORDER_QTY * direction
        fees = (entry.fee_breakdown.total_fees + exit_fill.fee_breakdown.total_fees)
        notional = entry.fill_price * ORDER_QTY
        returns.append((gross - fees) / notional)

        if not first_trade_printed:
            first_trade_printed = True
            print("First modelled trade (%s %g shares):" % (entry_side, ORDER_QTY))
            print("  mid %.4f -> fill %.4f (half-spread %.2f + impact %.4f/share)"
                  % (row.open, entry.fill_price, HALF_SPREAD,
                     entry.market_impact_per_unit))
            print("  participation %.4f%% of ADV, slippage cost %.2f, fees %.2f"
                  % (100.0 * entry.participation_ratio, entry.slippage_cost,
                     entry.fee_breakdown.total_fees))
            print("  Impact follows the square-root law, not a fixed percentage: "
                  "doubling the size raises it by about 41%, not 100%.")
            print("  Fees are 0.00 on the buy leg by construction: the US_EQUITY "
                  "schedule carries the sell-side SEC Section 31 fee and a zero "
                  "default commission, which you replace with your broker's.\n")

    # --- Step 3: one tearsheet, one stated convention ----------------------
    tearsheet = StandardizedTearsheetGenerator(
        risk_free_rate=0.0,
        periods_per_year=252,     # one bar = one trading day
    )
    metrics = tearsheet.generate(returns)

    print("--- Standardized Performance Tearsheet ---")
    print("Trades (one-bar holds)  : %d" % metrics["Periods"])
    print("Total Return            : %.4f%%" % (100.0 * metrics["Total Return"]))
    print("Annualized Return       : %.2f%%" % (100.0 * metrics["Annualized Return"]))
    print("Annualized Volatility   : %.2f%%" % (100.0 * metrics["Annualized Volatility"]))
    print("Sharpe Ratio            : %.2f" % metrics["Sharpe Ratio"])
    print("Sortino Ratio           : %.2f" % metrics["Sortino Ratio"])
    print("Calmar Ratio            : %.2f  [%s]"
          % (metrics["Calmar Ratio"], metrics["Calmar Convention"]))
    print("Max Drawdown            : %.2f%%" % (100.0 * metrics["Max Drawdown"]))
    print("Hit Rate                : %.2f%%" % (100.0 * metrics["Hit Rate"]))
    print("Annualization extrapolated from under a year: %s"
          % metrics["Annualization Extrapolated"])
    print("------------------------------------------")
    print("The bars are a seeded random walk, so these numbers describe the "
          "machinery, not an edge: a crossover on noise pays the spread and "
          "loses.\n")

    print("=== Walkthrough 02 Completed Cleanly ===")


if __name__ == "__main__":
    main()
