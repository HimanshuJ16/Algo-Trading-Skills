# Standards for Strategy Underperformance Remediation Decision Tree

| Node / Metric | Standard Threshold | Remediation Action Triggered |
|---|---|---|
| Hypothesis Validity | `is_alpha_hypothesis_valid = False` | `MANDATORY_STRATEGY_DECOMMISSION` |
| Slippage-to-Alpha Ratio | $> 50.0\%$ | `OPTIMIZE_EXECUTION_AND_DATA` |
| Peer Benchmark Sharpe | $< 0.50$ (Both Strategy & Peer $< 0.50$) | `TEMPORARY_CAPITAL_DEGRADE_RETAIN_SIGNAL` |
| Idiosyncratic Sharpe | Strategy $< 1.0$ & Peer $\ge 0.50$ | `RECALIBRATE_MODEL_PARAMETERS` |