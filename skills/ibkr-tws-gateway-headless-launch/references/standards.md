# Broker & Framework Coverage — ibkr-tws-gateway-headless-launch

## Default API Ports

All four are IBKR **defaults**, editable in Global Configuration → API → Settings. Verify
against the running instance rather than assuming.

| Environment / Service | Default API Port | socat relay port in `ghcr.io/gnzsnz/ib-gateway` | Protocol |
|---|---|---|---|
| IB Gateway (Paper) | `4002` | `4004` | TCP socket / TWS API |
| IB Gateway (Live) | `4001` | `4003` | TCP socket / TWS API |
| Trader Workstation TWS (Paper) | `7497` | `7499` (tws-rdesktop image) | TCP socket / TWS API |
| Trader Workstation TWS (Live) | `7496` | `7498` (tws-rdesktop image) | TCP socket / TWS API |

IB Gateway binds its API port to the container's `127.0.0.1` only; the relay port is what a
container publishes. `generate_docker_spec()` covers the IB Gateway image and raises on TWS
ports rather than emitting a mapping it has not been verified against.

## Restart & Session Events

| Event | Schedule | Socket impact | Authoritative signal |
|---|---|---|---|
| IBKR server reset | Per-region, published on IBKR's System Status page in local exchange time (ET / CET) | Local listener usually stays up | TWS API codes 1100 → 1101 / 1102; 2110 |
| IB Gateway auto-restart | Operator-configured (`AutoRestartTime` / `AUTO_RESTART_TIME`), in the host/container timezone | Listener drops and returns | Socket probe (`monitor_gateway_health`) |
| Weekly credential expiry | Sundays 01:00 ET | Session cannot restart unattended | Login required (second factor) |

US Eastern is EST (UTC-5) only in winter and EDT (UTC-4) from mid-March to early November,
so no single UTC constant describes the ET-quoted windows year-round.

## Relevant TWS API Message Codes

| Code | Meaning |
|---|---|
| 326 | "Unable to connect as the client id is already in use. Retry with a unique client id." |
| 502 | "Couldn't connect to TWS." Socket clients not enabled, or port mismatch. |
| 504 | "Not connected." Request issued without a live connection. |
| 1100 | "Connectivity between IB and the TWS has been lost." |
| 1101 | Connectivity restored — **data lost**; market data requests must be resubmitted. |
| 1102 | Connectivity restored — data maintained. |
| 2110 | "Connectivity between TWS and server is broken. It will be restored automatically." Typical during nightly resets. |

## Sources

| Claim | Source |
|---|---|
| TWS default socket ports 7496 (live) / 7497 (paper); Read-Only API enabled by default; Master Client ID behaviour; API settings must be enabled before a client can connect | TWS API — Initial Setup, https://interactivebrokers.github.io/tws-api/initial_setup.html |
| Post-socket version handshake; `nextValidId` marks connection completion; calls before it "could be dropped"; 32 clients per session; client id distinguishes clients | TWS API — Connectivity, https://interactivebrokers.github.io/tws-api/connection.html |
| Message codes 326 / 502 / 504 / 1100 / 1101 / 1102 / 2110 | TWS API — Message Codes, https://interactivebrokers.github.io/tws-api/message_codes.html |
| Per-region server reset schedule quoted in ET / CET | IBKR Current System Status, https://www.interactivebrokers.com/en/software/systemStatus.php (page blocks automated fetch; read it in a browser) |
| Weekly re-authentication: security tokens invalidated "each Sunday at 1:00 am ET" | IBKR Guides — Auto Restart Considerations, https://www.ibkrguides.com/traderworkstation/auto-restart-considerations.htm |
| Single login per week under IBC; authentication needed "the first time during the week that TWS or Gateway run after 01:00 ET on Sunday"; `AutoRestartTime`; 2FA timeout settings; `ExistingSessionDetectedAction`; config-file credential storage preferred over command line | IBC User Guide, https://github.com/IbcAlpha/IBC/blob/master/userguide.md |
| Image name `ghcr.io/gnzsnz/ib-gateway`; compose port mapping `127.0.0.1:4001:4003` / `127.0.0.1:4002:4004`; socat relays because Gateway binds container loopback; environment variables (`TWS_USERID`, `TWS_PASSWORD_FILE`, `TRADING_MODE`, `READ_ONLY_API`, `AUTO_RESTART_TIME`, `TIME_ZONE`, `TWOFA_TIMEOUT_ACTION`, `EXISTING_SESSION_DETECTED_ACTION`); warning that an exposed API port lets "every device on [the network] access your IB account" | gnzsnz/ib-gateway-docker README, https://github.com/gnzsnz/ib-gateway-docker |
| Image is Ubuntu-based and installs socat, xvfb, x11vnc, curl — but not netcat | gnzsnz/ib-gateway-docker stable Dockerfile, https://github.com/gnzsnz/ib-gateway-docker/blob/master/stable/Dockerfile |
| Top-level Compose `version` is obsolete and emits a warning | Docker Compose file reference, https://docs.docker.com/reference/compose-file/version-and-name/ |

Unverified at time of writing: the exact reset windows per region could not be fetched
programmatically (IBKR returns HTTP 403 to automated requests), so this skill deliberately
does not hard-code them — read the System Status page directly.

## Regulatory & Operational Notes

No jurisdiction-specific regulatory requirement is asserted by this skill. It intersects
with automated trading daemon supervision, containerized bot operations, credential custody,
and network exposure of an unauthenticated order-entry socket — the last of which is an
operational security control, not a regulatory one.
