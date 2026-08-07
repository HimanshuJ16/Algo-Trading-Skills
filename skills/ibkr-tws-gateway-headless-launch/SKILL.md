---
name: ibkr-tws-gateway-headless-launch
description: Use when deploying an Interactive Brokers (IBKR) trading bot to a headless
  server or container to manage IB Gateway / IBC headless startup, port probes, daily
  reset handling, and socket readiness checks
domain: algorithmic-trading
subdomain: broker-integration
tags:
- broker-integration
- ibkr
- ib-gateway
- tws-api
- docker-headless
brokers_frameworks:
- Interactive Brokers TWS API
- IB Gateway
- IBC
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever an Interactive Brokers (IBKR) strategy bot runs on a cloud VM, server, or Docker container with no persistent GUI desktop. IBKR requires IB Gateway or Trader Workstation (TWS) to run as a local proxy daemon between the Python API client (`ibapi` or `ib_insync`) and IBKR servers. Managing headless launch via Virtual Framebuffer (`Xvfb`), auto-login scripting (`IBC`), socket readiness probing on ports 4001/4002, and handling IBKR's mandatory daily server reset (typically 23:45 EST) is essential to avoid silent disconnects.

## Prerequisites

- IB Gateway installation and IBC configuration files (`config.ini`).
- API Ports: `4001` (Live API) or `4002` (Paper API), or TWS ports `7496` (Live) / `7497` (Paper).
- Headless environment with `Xvfb` or Docker container image (`ghcr.io/gnzrb/ib-gateway`).

## Workflow

1. **Configure IB Gateway & Port Specs**:
   - Specify connection parameters: `host`, `port` (4001 for Live, 4002 for Paper), `client_id`, and `is_paper` boolean.

2. **Headless Socket Readiness Probe**:
   - Before attempting IB API connection, execute TCP socket probe `probe_gateway_port()` to confirm IB Gateway is listening and ready to accept API clients.

3. **IBC Auto-Login & Process Supervision**:
   - Manage IB Gateway process under `Xvfb` or Docker supervision. Verify IBC handles authentication and bypasses GUI prompts automatically.

4. **Daily Server Reset Reconnection Protocol**:
   - IBKR forces a daily server reset every night (~23:45 EST / 04:45 UTC).
   - Implement `monitor_gateway_health()` to detect reset disconnects and trigger socket reconnect when Gateway re-opens.

5. **Docker Container Configuration Helper**:
   - Generate production Docker compose / service spec defining container restart policies, healthcheck probes, and credential security.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Unmonitored Daily Server Reset**: Failing to handle IBKR's mandatory 23:45 EST reset, leaving the trading bot hanging on a dead socket.
- **Connecting Before Gateway Readiness**: Attempting `ibapi` connect calls before IB Gateway completes initialization, triggering socket refused errors.
- **Port Conflict Between Live and Paper**: Using Live API port (4001) for Paper testing or vice versa.
- **GUI Dialog Hangs**: Running IB Gateway without IBC automation, causing the process to hang on unhandled modal dialogs.

## Verification

- Execute `probe_gateway_port(host, port)` and verify it returns `True` when Gateway is listening and `False` when offline.
- Simulate IB Gateway daily reset disconnection and verify `monitor_gateway_health()` detects disconnect and re-probes port.
- Generate Docker compose spec and verify port mapping (4001/4002) and healthcheck directives.
- Run unit test suite `python scripts/test_ib_headless_manager.py` and confirm 100% pass rate.

## Related Skills

- `systemd-supervision-for-trading-bots`
- `websocket-reconnect-without-duplicate-subscriptions`
- `token-lifecycle-live-probing`
---
