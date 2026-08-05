# Workflows for Risk Control Unit Testing Framework

1. **Scenario Construction**:
   - Define test cases covering normal orders, limit breaches, boundary edges, and price collars.
2. **Order Evaluation**:
   - Evaluate proposed order through pre-trade risk engine and measure decision latency.
3. **Assertion Verification**:
   - Assert expected vs actual rejection decisions and specific triggered risk rule codes.
4. **Report & CI/CD Gate Generation**:
   - Output structured execution report and block CI/CD pipeline if any risk unit test fails.