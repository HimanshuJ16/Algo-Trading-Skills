# Standards for Adversarial Robustness of Trading Signals

| Metric | Target Standard | Intervention Required |
|---|---|---|
| **Epsilon Constraint ($\epsilon$)** | Equivalent to 1 average Bid-Ask Spread | Set dynamically per instrument |
| **Vulnerability Score (Signal Flips)** | $< 5.0\%$ | Reject model deployment |
| **Noise Generation Type** | `worst_case_sign` | Gaussian noise is insufficient for stress-testing ML boundaries. |

## Category
`financial-ml-robustness`
