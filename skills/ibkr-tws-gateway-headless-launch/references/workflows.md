# Deep Workflow Reference — ibkr-tws-gateway-headless-launch

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Port & Trading Mode Verification:**
   - `is_paper=True` expects API port `4002` (Gateway) or `7497` (TWS).
   - `is_paper=False` expects API port `4001` (Gateway) or `7496` (TWS).
   - `IBGatewayHeadlessManager` vetoes startup on a mismatch in *either* direction before
     any socket is opened.
   - These four values are IBKR defaults, not protocol constants — the socket port is
     editable in Global Configuration → API → Settings. A custom port cannot be classified
     as paper or live, so the manager logs a warning and defers to the operator.
   - Assign a unique `client_id` per connecting process (TWS API error 326 on collision;
     32 concurrent clients maximum per Gateway session).

2. **Headless Xvfb / Container Execution:**
   - IB Gateway is a Java desktop application. On a headless Linux host it needs a virtual
     display — run it under `Xvfb`, or use `ghcr.io/gnzsnz/ib-gateway`, which ships Xvfb,
     x11vnc, IBC and socat on an Ubuntu base.
   - Configure IBC (`config.ini`) for automated login. Keep credentials in the config file
     protected by filesystem permissions, or in a container secret — not on a command line.
   - Set `AutoRestartTime` / `AUTO_RESTART_TIME` so the session restarts on a schedule, and
   - set the container `TIME_ZONE` to the zone that restart time is expressed in. The
     default is `Etc/UTC`, so an ET restart time with the default TZ fires at the wrong hour.
   - Decide `TWOFA_TIMEOUT_ACTION` and `ExistingSessionDetectedAction` deliberately: an
     IBKR Mobile or Client Portal login elsewhere can evict the headless session.

3. **Socket Readiness Probe:**
   - Call `IBGatewayHeadlessManager.wait_for_gateway_ready()` before constructing the
     `ibapi` / `ib_insync` client. It returns True or raises `IBGatewayError`.
   - An unresolvable host raises immediately instead of retrying a permanent fault.
   - The probe proves a listener exists. It does not prove the API session is usable —
     complete the version handshake and wait for `nextValidId` before sending requests.

4. **Restart Recovery — three distinct events:**

   | Event | Cadence | Effect on the local socket | Where to handle it |
   |---|---|---|---|
   | IBKR server reset | Per-region schedule published on IBKR's System Status page, quoted in local exchange time (ET / CET) | Usually stays up | API client: codes 1100 → 1101 (resubscribe) / 1102 (data maintained); 2110 |
   | Gateway auto-restart | Whatever `AUTO_RESTART_TIME` is set to | Listener drops and returns | `monitor_gateway_health()` |
   | Weekly credential expiry | Sundays 01:00 ET | Session cannot restart unattended | Operator / approved 2FA path |

   - Do not hard-code a UTC instant for the server reset: US Eastern is EST (UTC-5) only in
     winter and EDT (UTC-4) from mid-March to early November, and the windows differ by
     region. Read the live schedule for the server farm serving your account.
   - `monitor_gateway_health()` requires `unhealthy_threshold` consecutive failed probes
     before declaring a disconnect, so a single dropped probe during a restart does not
     trigger a spurious teardown. It reports measured downtime on recovery.
   - After reconnecting, rebuild client state: a restarted Gateway retains no prior client
     session or market-data subscriptions.

5. **Container Spec Generation:**
   - `generate_docker_spec()` emits a Compose spec with four non-default invariants:
     loopback port binding, host port mapped to the image's socat relay port (4003 live /
     4004 paper, since Gateway listens only on the container's `127.0.0.1`), `READ_ONLY_API`
     following `config.read_only_api` (default `yes`), and a bash `/dev/tcp` healthcheck.
   - The top-level Compose `version` key is omitted — the Compose Specification marks it
     obsolete and warns when it is present.
   - `restart: unless-stopped`, so a container an operator stopped during an incident is
     not resurrected by a Docker daemon restart.
   - The password is passed as a Compose secret via `TWS_PASSWORD_FILE` unless
     `use_password_file=False`.
   - Pin a concrete image tag in production instead of `stable`/`latest`, so an upstream IB
     Gateway release cannot change your runtime unannounced.

## Known Failure Modes

- **Port Misconfiguration:** connecting a paper bot to port 4001 and executing against live
  capital.
- **Network-Exposed API Port:** publishing `4001:4001` binds every host interface. The TWS
  API socket has no credential of its own — access control is the Gateway's Trusted-IPs
  list — so anything that can route to the host can place orders.
- **Wrong Container-Side Port:** mapping `4002:4002` against the gnzsnz image targets a port
  that only listens on the container's loopback; the relay is on 4004.
- **Healthcheck That Can Never Pass:** `nc -z` in an image with no netcat installed leaves
  the container permanently unhealthy, blocking anything gated on `service_healthy`.
- **Premature Connection:** treating a successful TCP probe as API readiness and sending
  requests before `nextValidId`.
- **Unmonitored Gateway Restart:** hanging on a socket that the scheduled Gateway restart
  closed.
- **DST Drift:** a reconnect window pinned to a fixed UTC instant derived from "EST" drifts
  an hour for roughly eight months of the year.

## Production Implementation Reference

- Reference code: `scripts/ib_headless_manager.py` (`IBGatewayHeadlessManager`,
  `IBGatewayConfig`, `GatewayHealthReport`).
- Automated unit tests: `scripts/test_ib_headless_manager.py`.
