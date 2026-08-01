# Standards for Reinforcement Learning Safety Constraints for Execution

| Metric | Engineering Standard |
|---|---|
| Max Order Size | Order size MUST NOT exceed $max\_order\_size$ per single slice. |
| Position Cap | Total inventory MUST NOT exceed $max\_inventory$ at any time. |
| Terminal Horizon | Forced inventory liquidation MUST trigger when $time\_remaining \le 60\text{s}$. |
| Reward Penalty | Intercepted actions MUST be penalized by deducting $\lambda \ge 10.0$ from step reward. |
