# Standards for Target Encoding

| Standard | Description |
|---|---|
| Zero Lookahead | Encoding statistics for $T_0$ must *strictly* be derived from $T_{-1}, T_{-2}, \dots$ |
| Smoothing Weight | The smoothing `weight` acts as a prior. A weight of `20` means it takes 20 observations for the local mean to carry 50% of the weight against the global mean. |
| Cold Start Fallback | Any unknown or new symbol must seamlessly fall back to the expanding `global_mean`. |
