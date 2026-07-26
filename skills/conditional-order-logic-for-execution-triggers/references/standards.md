# Standards for Conditional Order Logic

| Metric | Engineering Standard |
|---|---|
| Single-Fire Guarantee | A triggered conditional order MUST fire exactly once and immediately transition to `TRIGGERED` state. |
| Evaluation Microsecond Budget | Condition tree evaluation per market tick MUST complete in $< 5\ \mu\text{s}$. |
| Stale Benchmark Handling | If a referenced cross-asset symbol quote age exceeds 5 seconds, condition evaluation MUST evaluate to `FALSE`. |
