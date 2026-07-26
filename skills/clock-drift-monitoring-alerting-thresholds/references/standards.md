# Standards for PTP Clock Sync

| Metric | Engineering Standard |
|---|---|
| Regulatory Limit | Under MiFID II RTS 25, HFT algorithmic trading flow MUST NOT exceed 100 microseconds (0.0001 seconds) of drift from UTC. |
| Kill-Switch Latency | The monitoring process must trigger the trading engine kill-switch in under 5 milliseconds from the moment a PTP log reports a critical breach. |
| Holdover Handling | If the server loses connection to the Grandmaster clock, it enters `HOLDOVER` state. The monitor must start a timer. If the server cannot lock to a backup Grandmaster before its local oscillator drifts beyond 100µs (based on hardware spec), trading must be halted. |
