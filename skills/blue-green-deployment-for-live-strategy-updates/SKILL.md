---
name: Blue-Green Deployment for Live Strategy Updates
description: Institutional quant standards for zero-downtime, state-synchronized blue-green deployments for live trading systems.
---

# Blue-Green Deployment for Live Strategy Updates

## Overview
This skill provides robust, institutional-grade standards and implementation patterns for deploying quantitative trading strategies via blue-green deployment. This approach guarantees zero-downtime cutovers, continuous market data ingestion, and rapid, state-aware rollbacks. 

## Objectives
- **Zero Market Disruption:** Ensure continuous trading presence and uninterrupted market data processing.
- **Risk Mitigation:** Run extensive risk and health checks on new strategy instances (Green) before transferring live order routing.
- **Atomic Cutovers:** Transfer live portfolio state and order routing instantaneously to the new version.
- **Immediate Rollback:** Retain the prior version (Blue) in a standby/draining state to allow sub-second rollback if anomalies are detected.

## Structure
- `scripts/blue_green_deployer.py`: The robust implementation with state syncing and thread safety.
- `scripts/test_blue_green_deployer.py`: Thorough unit tests validating cutover and rollback paths.
- `references/workflows.md`: Detailed CI/CD to live trading deployment pipelines.
- `references/standards.md`: Quant-specific engineering requirements for zero-gap state transitions.
- `assets/checklist.md`: The critical path checklist for trading operations teams.

## Usage
Consult the `references` for architectural standards, and utilize `scripts/blue_green_deployer.py` to manage active vs inactive slots in your quantitative execution engine.
