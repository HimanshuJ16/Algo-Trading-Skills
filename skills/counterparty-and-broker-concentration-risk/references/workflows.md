# Deep Workflow Reference — counterparty-and-broker-concentration-risk

## Full Procedure

1. Register all brokers/custodians with capital held.
2. Compute concentration: capital_at_counterparty / total_AUM.
3. Before transferring additional capital, verify projected concentration ≤ limit.
4. Monitor for drift as P&L shifts balances between counterparties.

## Production Implementation Reference

- Code: `scripts/counterparty_monitor.py` (`CounterpartyConcentrationMonitor`).
- Tests: `scripts/test_counterparty_monitor.py`.
