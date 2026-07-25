# Broker Integration Standards — demo-account-realism-gap-assessment

| Metric | Target / Benchmark | Description |
|---|---|---|
| Latency Score Weight | 30% | Ratio of Demo vs Live execution delay |
| Slippage Score Weight | 40% | Exponential decay penalty for unaccounted slippage |
| Fill Rate Weight | 30% | Ratio of Live vs Demo partial fills |
| Realism Score Target | $R \ge 0.75$ | Minimum acceptable fidelity score for live promotion |

## Category

`broker-integration` — see top-level `mappings/` directory.
