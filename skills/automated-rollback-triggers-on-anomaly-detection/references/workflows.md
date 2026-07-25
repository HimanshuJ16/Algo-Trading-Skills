# Workflows for Automated Rollbacks

1. **Deployment Phase**: A new algorithmic trading bot container is spun up in production (e.g., via Kubernetes).
2. **Telemetry Ingestion**: For a defined "burn-in" period (e.g., 15 minutes), Prometheus aggregates latency, error rates, and order execution statistics.
3. **Anomaly Evaluation**: A cronjob or continuous polling service pulls the metrics and feeds them into `AutomatedRollbackEngine.evaluate_metrics()`.
4. **Trigger Hook**: If `should_rollback == True`, the script immediately triggers a webhook to the CI/CD controller (e.g., ArgoCD).
5. **Reversion**: The CI/CD controller automatically scales down the new container and scales up the previously successful container, restoring the system to the "last known good" state.