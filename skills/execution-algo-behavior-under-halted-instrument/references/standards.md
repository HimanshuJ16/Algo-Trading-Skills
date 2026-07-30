# Standards for Execution Algo Behavior Under Halted Instrument

| Metric | Engineering Standard |
|---|---|
| Child Order Cancel SLA | All resting child orders MUST be cancelled within $< 50\text{ms}$ of halt event. |
| Timer Suspension | Algo slice timers MUST pause during active trading halts. |
| Backlog Smoothing | Missed slice quantities MUST be smoothed over remaining execution horizon. |