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
version: "1.1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever an Interactive Brokers (IBKR) strategy bot runs on a cloud VM, server, or Docker container with no persistent GUI desktop. IBKR publishes no direct-to-server socket API for trading: the Python client (`ibapi` or `ib_insync`) talks to a locally running IB Gateway or Trader Workstation (TWS) process, which holds the authenticated session. That process is a Java desktop application, so a headless host needs a virtual display (`Xvfb`) plus login automation (IBC) — or a container image that bundles both.

This skill covers the *transport and process layer*: paper/live port guarding, socket readiness probing, detecting the Gateway restarts that drop your socket, and generating a container spec that does not expose order entry to the network.

## When NOT to Use

- **You are choosing an authentication archetype**, not operating an already-chosen one — start at `headless-broker-auth-patterns`, which classifies IBKR as a supervised-gateway broker and explains why that constrains what "unattended" can mean.
- **You need API-level readiness or reconnection semantics.** A TCP probe cannot tell you whether the API session is usable. Handshake completion (`nextValidId`), client-id collisions (error 326), and connectivity codes 1100/1101/1102 belong in your API client, not here.
- **You are running IBKR's Client Portal Web API or the CP Gateway.** Different process, different ports, different auth model; none of the port logic here applies.
- **You expect true 24/7 unattended operation.** IBKR invalidates session credentials weekly (Sundays 01:00 ET), and re-authentication involves a second factor. Plan an operator touchpoint or an approved 2FA path; do not design as if the session never expires.

## Prerequisites

- IB Gateway (or TWS) installed, plus an IBC `config.ini` for automated login. IBC's user guide states the config-file method is preferred "because the configuration file can be protected by the operating system", and calls passing the password on the command line "strongly deprecated".
- A headless display: `Xvfb`, or a container image such as `ghcr.io/gnzsnz/ib-gateway` that ships Xvfb, IBC and a socat relay.
- API access enabled on the Gateway/TWS side, with the intended socket port and Trusted IPs configured. IBKR ships **Read-Only API enabled by default** "as an additional precautionary measure" — order entry requires deliberately turning it off.
- Default API ports to verify (all are user-configurable, not guaranteed): IB Gateway `4001` live / `4002` paper; TWS `7496` live / `7497` paper.

## Workflow

1. **Bind the trading mode to the port before anything else.**
   - Construct `IBGatewayConfig(host, port, client_id, is_paper=...)`. `IBGatewayHeadlessManager` refuses a paper config on a live default port *and* a live config on a paper default port.
   - A non-default port cannot be classified either way: the manager logs a warning and proceeds. If you run a custom socket port, you own that verification — confirm which account the Gateway is logged into before enabling order entry.
   - Give every process its own `client_id`. A duplicate is rejected with TWS API error 326, and a Gateway session accepts at most 32 concurrent clients.

2. **Probe the socket, then confirm readiness at the API layer.**
   - Call `wait_for_gateway_ready()` before constructing the API client. It returns True or raises — it never returns False, so a caller cannot fall through to a dead socket.
   - An unresolvable host raises immediately rather than consuming the retry budget: a DNS fault is a permanent configuration error, not a "still starting" condition.
   - **A successful probe is a precondition, not readiness.** IBKR's connectivity documentation states that after the socket opens "there must be an initial handshake" and that `nextValidId` "is commonly used to indicate that the connection is completed", warning that "function calls made prior to this time could be dropped by TWS". Gate order submission on `nextValidId`, not on the probe.

3. **Run IB Gateway under IBC with a scheduled restart.**
   - Set `AutoRestartTime` (IBC `config.ini`) or `AUTO_RESTART_TIME` (container). Without it the session simply ages out; with it, one login at the start of the week carries through — IBC's guide notes authentication is needed only "the first time during the week that TWS or Gateway run after 01:00 ET on Sunday."
   - The restart time is interpreted in the **container's** timezone. Setting `AUTO_RESTART_TIME` to an ET value while leaving `TIME_ZONE` at its `Etc/UTC` default schedules the wrong instant — set both together.
   - Decide the 2FA timeout behaviour explicitly (`TWOFA_TIMEOUT_ACTION`, `ExistingSessionDetectedAction`). An IBKR Mobile login elsewhere can evict this session.

4. **Distinguish the three restart events; only one of them drops your socket.**
   - *IBKR server reset* — published per region on IBKR's System Status page, quoted in local exchange time (ET/CET). The local API socket generally stays up; you observe code 1100 followed by 1101 (resubscribe market data) or 1102 (subscriptions maintained). Handle this in the API client.
   - *Gateway auto-restart* — the listening socket disappears and returns. This is what `monitor_gateway_health()` detects: it declares a disconnect only after `unhealthy_threshold` consecutive failed probes, so one dropped probe does not tear down a live strategy, and it reports measured downtime on recovery.
   - *Weekly credential expiry* — Sundays 01:00 ET. No amount of socket retrying fixes it; it needs a real login.
   - After any reconnect, rebuild client state. A restarted Gateway retains no prior client session or market-data subscriptions.

5. **Generate the container spec with `generate_docker_spec()` and deploy it unmodified.**
   - The published port binds to `bind_address` (loopback by default) and maps to the image's **socat relay** port (4003 live / 4004 paper), because Gateway itself listens only on the container's `127.0.0.1`. Mapping `4001:4001` targets a port nothing serves externally.
   - `READ_ONLY_API` follows `config.read_only_api`, which defaults to `yes`. Turning it off on a live account is logged as a warning.
   - The password is delivered as a Compose secret (`TWS_PASSWORD_FILE`) by default rather than an environment variable.
   - The healthcheck uses bash `/dev/tcp`; the image ships socat and bash but **not** netcat, so an `nc -z` healthcheck can never pass there.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table and sources: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Publishing the API port on `0.0.0.0`.** The TWS API socket carries no credential of its own — access control is the Gateway's Trusted-IPs list. A `4001:4001` mapping in Compose publishes order entry on every host interface. The image's own documentation warns that otherwise "every device on [the network] can access your IB account". Bind to loopback, or use an SSH tunnel or a shared Docker network.
- **Treating a successful TCP connect as "ready to trade".** Gateway can be listening while still logged out, read-only, or about to reject your client id. Requests sent before `nextValidId` can be silently dropped.
- **Hard-coding the reset as "23:45 EST / 04:45 UTC".** US Eastern is EST (UTC-5) only in winter; from mid-March to early November it is EDT (UTC-4), so a fixed UTC instant is wrong for most of the year. IBKR also publishes *different* windows per server region. Read the published schedule for your account's server farm rather than pinning a constant.
- **Conflating the IBKR server reset with the Gateway restart.** The server reset usually leaves your socket up and surfaces as codes 1100/1101/1102; the Gateway auto-restart is what actually closes the listener. Code that only watches for socket loss misses the first; code that only watches API error codes misses the second.
- **Retrying a socket probe against an unresolvable hostname.** A typo in the host burns the entire retry budget producing "not ready yet" logs for a fault that will never clear.
- **`restart: always` on the Gateway container.** An operator who stops the Gateway during an incident will have it resurrected by the next Docker daemon restart. Use `unless-stopped` so a deliberate stop stays a stop.
- **Reusing one `client_id` across strategies.** The second connection is rejected (error 326), and if it is the reconnecting process that loses, a restart can leave the bot permanently unable to attach.
- **Assuming the container timezone matches your restart-time string.** `AUTO_RESTART_TIME` is read in the container's `TIME_ZONE`, which defaults to `Etc/UTC`.

## Verification

- Run the unit suite: `python -m unittest discover -s skills/ibkr-tws-gateway-headless-launch/scripts` — all tests must pass.
- Construct a live-mode config on a paper port and a paper-mode config on a live port; confirm both raise `IBGatewayError` before any socket is opened.
- Call `wait_for_gateway_ready()` against a closed port and confirm it raises rather than returning False; against a listening socket, confirm it returns True on the first probe without sleeping.
- Drive `monitor_gateway_health()` with a bounded `max_polls` and an injected `sleep_fn`/`clock_fn`, close the listening socket mid-run, and confirm `disconnect_events`/`reconnect_events` and measured downtime in the returned `GatewayHealthReport`.
- Dump `generate_docker_spec()` to YAML and run `docker compose config` against it. Confirm the resolved output shows `host_ip: 127.0.0.1`, `target` equal to the socat relay port, `published` equal to the API port, `READ_ONLY_API: "yes"`, `restart: unless-stopped`, and the secret mounted at the path named by `TWS_PASSWORD_FILE`.
- Confirm the healthcheck command uses only binaries present in your chosen image.

## Related Skills

- `headless-broker-auth-patterns`
- `systemd-supervision-for-trading-bots`
- `websocket-reconnect-without-duplicate-subscriptions`
- `token-lifecycle-live-probing`
