# Broker Integration Standards — broker-failover-secondary-account-routing

| Parameter | Specification | Description |
|---|---|---|
| Failure Threshold | 3 consecutive failures | Breaker tripping limit |
| Health States | `HEALTHY`, `DEGRADED`, `DOWN` | Circuit breaker status enum |
| Symbol Mapping | Canonical -> Broker Symbol Dict | Cross-broker symbol translation |
| Routing Priority | Primary -> Secondary | Order dispatch flow |

## Category

`broker-integration` — see top-level `mappings/` directory.
