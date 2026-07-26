# Workflows for Chaos Engineering

1. **Test Environment Setup**:
   - Spin up the Trading Engine, Market Data Feed Handler, and Order Management System in an isolated Docker network.
2. **Experiment Definition**:
   - Hypothesis: "If the Feed Handler process is killed, the Trading Engine will detect the missing heartbeat within 3 seconds, cancel all open orders via the OMS, and enter a HALTED state."
3. **Execution**:
   - The CI/CD pipeline triggers the `ChaosInjector` to send a `SIGKILL` to the Feed Handler container.
4. **Validation**:
   - The testing framework waits 5 seconds and queries the Trading Engine's state.
   - If State == `HALTED` and Open Orders == 0, the test PASSES.
   - If State == `ACTIVE` or Open Orders > 0, the test FAILS, indicating a critical resilience flaw.
5. **Teardown**: Restore the environment to baseline.
