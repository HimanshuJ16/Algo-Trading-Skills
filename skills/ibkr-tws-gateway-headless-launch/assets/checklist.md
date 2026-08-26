# Pre-Flight / Sign-off Checklist — ibkr-tws-gateway-headless-launch

Use this before considering the skill's implementation complete.

## Mode & Connection

- [ ] **Port & Mode Matching:** Port `4002` (Paper) vs `4001` (Live) matches the configured
      `is_paper` value, and `IBGatewayHeadlessManager` construction was allowed to run the
      guard rather than being bypassed.
- [ ] **Custom Port Ownership:** If a non-default socket port is in use, the account the
      Gateway is logged into has been confirmed manually — the automatic guard cannot
      classify it.
- [ ] **Unique Client ID:** Every process connecting to this Gateway has its own
      `client_id` (collision → TWS API error 326; 32 clients maximum).

## Readiness

- [ ] **Socket Readiness Probe:** `wait_for_gateway_ready()` runs before the API client is
      constructed, and its raise-on-exhaustion behaviour is not swallowed by the caller.
- [ ] **API-Level Readiness:** Order submission is gated on `nextValidId`, not on the TCP
      probe.
- [ ] **Headless Automation:** IBC handles login without unhandled GUI modals, and the
      credential lives in a protected config file or container secret — never on a command
      line.

## Restart Resiliency

- [ ] **Scheduled Restart Configured:** `AutoRestartTime` / `AUTO_RESTART_TIME` is set, and
      the container `TIME_ZONE` matches the zone that time is expressed in.
- [ ] **Gateway Restart Detection:** `monitor_gateway_health()` (or equivalent supervision)
      watches for socket loss, with `unhealthy_threshold > 1` to suppress single-probe flaps.
- [ ] **Server Reset Handling:** The API client handles codes 1100 → 1101 / 1102 and
      resubscribes market data after 1101. No fixed UTC constant is hard-coded for the
      ET-quoted reset window.
- [ ] **Weekly Expiry Plan:** An operator touchpoint or approved 2FA path exists for the
      Sunday 01:00 ET credential invalidation.
- [ ] **State Rebuild:** Post-reconnect logic reconstructs client state; nothing assumes
      subscriptions or open-order streams survived the restart.

## Container Deployment

- [ ] **No Network Exposure:** The published API port binds to loopback (or an SSH tunnel /
      shared Docker network is used). `docker compose config` shows `host_ip: 127.0.0.1`.
- [ ] **Correct Relay Port:** The host port maps to the image's socat relay port (4003 live
      / 4004 paper), not to the Gateway port.
- [ ] **Read-Only Default Respected:** `READ_ONLY_API` is `yes` unless order entry was
      deliberately enabled, and that decision is recorded below.
- [ ] **Healthcheck Runs:** The healthcheck command uses only binaries present in the chosen
      image (the gnzsnz image has bash and socat, but no `nc`).
- [ ] **Restart Policy:** `unless-stopped`, so a deliberate operator stop is not undone by a
      Docker daemon restart.
- [ ] **Image Pinned:** A concrete version tag is used in production, not `latest`/`stable`.

## Testing

- [ ] **Automated Testing:** `python -m unittest discover -s skills/ibkr-tws-gateway-headless-launch/scripts`
      passes with no failures.
- [ ] **Compose Validated:** `docker compose config` accepts the generated spec and the
      resolved output matches the invariants above.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
- Order entry enabled (READ_ONLY_API=no)? If yes, approved by: ___________________________
