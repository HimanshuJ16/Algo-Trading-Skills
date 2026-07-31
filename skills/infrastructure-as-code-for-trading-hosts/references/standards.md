# Standards for IaC Trading Host Provisioning

| Metric | Engineering Standard |
|---|---|
| CPU Governor | CPU frequency governor MUST be set to `"performance"`. |
| C-State Disabling | Power-saving CPU C-states MUST be disabled (`max_cstate=0`). |
| Socket Buffer Size | Max receive buffer `net.core.rmem_max` MUST be $\ge 134,217,728$ bytes ($128\text{ MB}$). |
| PTP Clock Sync | PTP IEEE 1588 clock synchronization MUST be active (`ptp4l`). |
