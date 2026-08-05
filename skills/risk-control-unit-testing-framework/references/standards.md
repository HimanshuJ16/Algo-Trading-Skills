# Standards for Risk Control Unit Testing Framework

| Metric | Engineering Standard |
|---|---|
| Unit Test Pass Rate | 100% pass rate MUST be required for CI/CD build promotion. |
| Coverage Requirements | Unit tests MUST cover all registered pre-trade risk rules. |
| Evaluation Latency SLA | Pre-trade risk rule evaluation MUST complete within $< 100\,\mu\text{s}$ per order. |