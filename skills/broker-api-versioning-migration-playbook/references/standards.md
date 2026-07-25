# Quantitative Engineering API Migration Standards

When upgrading API versions connecting algorithmic trading engines to brokers/exchanges, the following quantitative standards must be met:

## 1. Latency Tolerances
- **Read APIs (REST):** The target version (V2) must not exhibit a mean latency degradation of more than `5%` compared to the legacy version (V1) during the shadow phase. 99th percentile (p99) latency spikes must not exceed `1.2x` the V1 p99.
- **Write APIs (Orders/Cancels):** Execution RTT must be statistically indistinguishable or improved. A two-sample t-test between V1 and V2 RTTs should yield a p-value > 0.05 (assuming no expected improvement) or show statistically significant improvement.

## 2. Schema and Type Strictness
- **Strict Typing:** Any drift in primitive types (e.g., float to string) detected during the shadow audit phase is considered a critical blocking failure.
- **Missing Fields:** If V2 deprecates fields used by downstream strategy logic, the migration is blocked until the downstream logic is refactored. `missing_in_v2` must evaluate to `0` for critical keys.

## 3. Concurrency Safety
- The migration engine itself (e.g., `BrokerAPIVersionMigrator`) must be guaranteed thread-safe. Asynchronous order loops or thread pools executing parallel shadow reads must not race on phase state changes or latency tracking arrays.

## 4. Rollback SLAs
- **Time to Rollback:** The system must be capable of reverting from `CANARY_CUTOVER` or `V2_ONLY` to `ROLLBACK_V1` in under 100 milliseconds without requiring an application restart.
