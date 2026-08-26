"""
ibkr-tws-gateway-headless-launch: IBKR IB Gateway headless launch manager -- paper/live
port guard, TCP socket readiness prober, gateway health monitor for daily-restart
recovery, and hardened Docker Compose spec generator.

READINESS BOUNDARY (read before treating a successful probe as "ready to trade"):
A successful TCP connect to the API port proves only that something is listening. It does
NOT prove the API session is usable. Per IBKR's TWS API connectivity documentation, after
the socket opens "there must be an initial handshake in which information is exchanged
about the highest version supported by TWS and the API", and the `nextValidId` callback
"is commonly used to indicate that the connection is completed", warning that "function
calls made prior to this time could be dropped by TWS". IB Gateway can also be listening
while still logged out, read-only, or refusing the client id (error 326). Treat
`probe_gateway_port` / `wait_for_gateway_ready` as a *precondition* that removes the
connection-refused failure mode, then confirm real readiness in your `ibapi` / `ib_insync`
client via `nextValidId`.

RESTART MODEL (three distinct events, frequently conflated):
1. IBKR *server* reset -- IBKR publishes a per-region reset schedule on its System Status
   page (https://www.interactivebrokers.com/en/software/systemStatus.php). The windows are
   quoted per region and in local exchange time (ET / CET), not in a single fixed UTC
   offset -- US Eastern is EST (UTC-5) only in winter and EDT (UTC-4) from mid-March to
   early November. Do not hard-code a UTC instant; read the published schedule for the
   server farm your account is routed to. During a reset the API socket usually stays up
   while connectivity to IBKR drops (TWS API codes 1100 -> 1101/1102, 2110).
2. IB Gateway *auto-restart* -- the Gateway process restarts itself at a configured local
   time (`AutoRestartTime` in IBC's config.ini, `AUTO_RESTART_TIME` in the container). This
   one does drop the listening socket, and is what `monitor_gateway_health` is for.
3. Weekly credential expiry -- IBKR invalidates session credentials each Sunday at
   01:00 ET, so the first start after that needs a real (2FA) login. Auto-restart carries
   a session through the week but cannot survive this.

Sources consulted are listed in `references/standards.md`.
"""
from dataclasses import dataclass
import logging
import re
import socket
import time
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

MAX_TCP_PORT = 65535

# Default IBKR API ports. TWS 7496 (live) / 7497 (paper) are stated in IBKR's TWS API
# documentation; IB Gateway 4001 (live) / 4002 (paper) are the documented image/desktop
# defaults. All four are user-configurable in Global Configuration -> API -> Settings,
# so treat them as defaults to verify, not as guarantees.
IB_PAPER_GATEWAY_PORT = 4002
IB_LIVE_GATEWAY_PORT = 4001
IB_PAPER_TWS_PORT = 7497
IB_LIVE_TWS_PORT = 7496

LIVE_PORTS = frozenset({IB_LIVE_GATEWAY_PORT, IB_LIVE_TWS_PORT})
PAPER_PORTS = frozenset({IB_PAPER_GATEWAY_PORT, IB_PAPER_TWS_PORT})

# Community IB Gateway + IBC container image. `stable` tracks IBKR's stable Gateway
# channel; pin a concrete version tag in production so an upstream Gateway release cannot
# change your runtime unannounced.
DEFAULT_IB_GATEWAY_IMAGE = "ghcr.io/gnzsnz/ib-gateway:stable"

# IB Gateway binds its API port to the container's 127.0.0.1 only, so the image runs a
# socat relay on a second port to accept connections from outside the container. Publish
# the relay port, not the Gateway port -- publishing 4001:4001 maps to a port nothing
# listens on externally.
SOCAT_RELAY_PORTS: Dict[int, int] = {
    IB_LIVE_GATEWAY_PORT: 4003,
    IB_PAPER_GATEWAY_PORT: 4004,
}

# The image is Ubuntu-based and ships socat + bash but NOT netcat, so an `nc -z`
# healthcheck can never pass in it. bash's /dev/tcp is always available; note that
# CMD-SHELL runs under /bin/sh, hence the explicit `bash -c`.
_HEALTHCHECK_TEMPLATE = "bash -c 'exec 3<>/dev/tcp/127.0.0.1/{port}' || exit 1"

_AUTO_RESTART_TIME_RE = re.compile(r"^(0?[1-9]|1[0-2]):[0-5][0-9] (AM|PM)$")

_LOOPBACK_BIND_ADDRESSES = frozenset({"127.0.0.1", "localhost", "::1"})


class IBGatewayError(RuntimeError):
    """Raised on invalid configuration, unresolvable host, or failed readiness probe."""


@dataclass(frozen=True)
class GatewayHealthReport:
    """Outcome of a `monitor_gateway_health` run."""

    polls: int = 0
    successful_probes: int = 0
    failed_probes: int = 0
    disconnect_events: int = 0
    reconnect_events: int = 0
    total_downtime_seconds: float = 0.0
    healthy_at_exit: bool = True


@dataclass
class IBGatewayConfig:
    """Connection and deployment parameters for one headless IB Gateway instance.

    Args:
        host: Address the API client connects to.
        port: API port on `host`. Must match `is_paper` -- see `_validate_port_matching`.
        client_id: TWS API client id. Must be unique across every process sharing this
            Gateway; a duplicate is rejected with TWS API error 326. A Gateway session
            accepts up to 32 concurrent clients.
        is_paper: True for the paper-trading account, False for live capital.
        timeout_seconds: Per-probe TCP connect timeout.
        read_only_api: Mirrors the Gateway "Read-Only API" setting, which IBKR enables by
            default "as an additional precautionary measure". Defaults to True here for
            the same reason: opting into order entry must be deliberate.
        bind_address: Host interface the container publishes the API port on. The TWS API
            socket has no authentication of its own beyond Gateway-side Trusted IPs, so a
            non-loopback bind exposes order entry to everything that can reach the host.
        image: Container image reference.
        auto_restart_time: Gateway auto-restart time as "hh:mm AM/PM", interpreted in
            `time_zone`. None means no scheduled restart is configured.
        time_zone: Container TZ. `auto_restart_time` is read in this zone, so leaving the
            default while quoting an ET restart time schedules the wrong instant.
        use_password_file: Pass the password via a Compose secret (TWS_PASSWORD_FILE)
            instead of an environment variable.
    """

    host: str = "127.0.0.1"
    port: int = IB_PAPER_GATEWAY_PORT
    client_id: int = 1
    is_paper: bool = True
    timeout_seconds: float = 5.0
    read_only_api: bool = True
    bind_address: str = "127.0.0.1"
    image: str = DEFAULT_IB_GATEWAY_IMAGE
    auto_restart_time: Optional[str] = None
    time_zone: str = "Etc/UTC"
    use_password_file: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.host, str) or not self.host.strip():
            raise IBGatewayError("host must be a non-empty string.")
        if isinstance(self.port, bool) or not isinstance(self.port, int):
            raise IBGatewayError(f"port must be an int, got {type(self.port).__name__}.")
        if not 1 <= self.port <= MAX_TCP_PORT:
            raise IBGatewayError(f"port {self.port} outside valid TCP range 1-{MAX_TCP_PORT}.")
        if isinstance(self.client_id, bool) or not isinstance(self.client_id, int) or self.client_id < 0:
            raise IBGatewayError(f"client_id must be a non-negative int, got {self.client_id!r}.")
        if not isinstance(self.timeout_seconds, (int, float)) or self.timeout_seconds <= 0:
            raise IBGatewayError(f"timeout_seconds must be > 0, got {self.timeout_seconds!r}.")
        if self.auto_restart_time is not None and not _AUTO_RESTART_TIME_RE.match(self.auto_restart_time):
            raise IBGatewayError(
                f"auto_restart_time {self.auto_restart_time!r} must be formatted 'hh:mm AM/PM' "
                "(the format IBC and the container image expect), e.g. '11:45 PM'."
            )


class IBGatewayHeadlessManager:
    """
    Guards paper/live port matching, probes IB Gateway socket readiness, monitors the
    socket across Gateway restarts, and generates a hardened container spec.

    This class deliberately does not speak the TWS API wire protocol. It manages the
    *transport precondition*; the API handshake belongs to your `ibapi` / `ib_insync`
    client. See the module docstring's READINESS BOUNDARY.
    """

    def __init__(self, config: IBGatewayConfig) -> None:
        self.config = config
        self._validate_port_matching()

    def _validate_port_matching(self) -> None:
        """Rejects a paper/live port that contradicts the configured trading mode.

        This guard only recognises IBKR's four default ports. A custom port cannot be
        classified, so it is allowed but logged -- with a custom port the operator, not
        this check, is responsible for confirming which account the Gateway is serving.
        """
        port = self.config.port
        if self.config.is_paper and port in LIVE_PORTS:
            raise IBGatewayError(
                f"CRITICAL PORT MISMATCH: paper mode configured, but port {port} is an "
                "IBKR LIVE default. Orders would be routed against live capital."
            )
        if not self.config.is_paper and port in PAPER_PORTS:
            raise IBGatewayError(
                f"CRITICAL PORT MISMATCH: live mode configured, but port {port} is an "
                "IBKR PAPER default."
            )
        if port not in LIVE_PORTS and port not in PAPER_PORTS:
            logger.warning(
                "Port %s is not an IBKR default port; paper/live mismatch cannot be "
                "verified automatically for is_paper=%s. Confirm the Gateway's configured "
                "socket port and account manually.",
                port,
                self.config.is_paper,
            )

    @staticmethod
    def probe_gateway_port(host: str, port: int, timeout: float = 2.0) -> bool:
        """Attempts one TCP connection to the IB Gateway API port.

        Returns True if the connection was accepted -- meaning only that a listener
        exists, not that the API session is usable (see module docstring).

        Raises:
            IBGatewayError: if `host` cannot be resolved. Name resolution failure is a
                permanent configuration fault, not a "not up yet" condition, so it is
                surfaced rather than folded into a retry loop that can never succeed.
        """
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except socket.gaierror as exc:
            raise IBGatewayError(f"Cannot resolve IB Gateway host {host!r}: {exc}") from exc
        except (socket.timeout, ConnectionRefusedError, OSError):
            return False

    def wait_for_gateway_ready(self, max_retries: int = 10, retry_interval: float = 2.0) -> bool:
        """Polls the API port until a listener accepts, or raises after `max_retries`.

        Returns True on success; never returns False (an exhausted budget raises so a
        caller cannot silently proceed to connect against a dead socket).

        Raises:
            IBGatewayError: on invalid arguments, unresolvable host, or exhausted retries.
        """
        if max_retries < 1:
            raise IBGatewayError(f"max_retries must be >= 1, got {max_retries}.")
        if retry_interval < 0:
            raise IBGatewayError(f"retry_interval must be >= 0, got {retry_interval}.")

        logger.info(
            "Waiting for IB Gateway on %s:%s (max_retries=%s).",
            self.config.host,
            self.config.port,
            max_retries,
        )
        for attempt in range(1, max_retries + 1):
            if self.probe_gateway_port(self.config.host, self.config.port, self.config.timeout_seconds):
                logger.info(
                    "IB Gateway socket accepting connections on %s:%s after %s attempt(s). "
                    "Confirm API readiness via nextValidId before sending requests.",
                    self.config.host,
                    self.config.port,
                    attempt,
                )
                return True
            # Do not sleep after the final attempt -- the budget is already exhausted.
            if attempt < max_retries:
                logger.warning(
                    "Gateway probe attempt %s/%s failed; retrying in %ss.",
                    attempt,
                    max_retries,
                    retry_interval,
                )
                time.sleep(retry_interval)

        raise IBGatewayError(
            f"IB Gateway on {self.config.host}:{self.config.port} failed to become ready "
            f"after {max_retries} attempts."
        )

    def monitor_gateway_health(
        self,
        poll_interval: float = 30.0,
        unhealthy_threshold: int = 2,
        max_polls: Optional[int] = None,
        on_disconnect: Optional[Callable[[int], None]] = None,
        on_reconnect: Optional[Callable[[float], None]] = None,
        *,
        sleep_fn: Callable[[float], None] = time.sleep,
        clock_fn: Callable[[], float] = time.monotonic,
    ) -> GatewayHealthReport:
        """Polls the API port and reports socket loss/recovery across Gateway restarts.

        Detects event (2) in the module docstring's restart model: the Gateway process
        restarting and dropping its listening socket. It does NOT detect event (1), an
        IBKR-side server reset, because the local socket typically stays up through it --
        that shows up in your API client as codes 1100 then 1101/1102, and must be handled
        there.

        A disconnect is only declared after `unhealthy_threshold` consecutive failed
        probes, so a single dropped probe during a restart does not trigger a spurious
        teardown of a live strategy.

        Args:
            poll_interval: Seconds between probes.
            unhealthy_threshold: Consecutive failures required to declare a disconnect.
            max_polls: Stop after this many probes. None runs until interrupted; pass an
                integer for bounded supervision and for tests.
            on_disconnect: Called with the consecutive-failure count when the socket is
                first declared down.
            on_reconnect: Called with observed downtime in seconds when it returns.
            sleep_fn: Injected sleep, for deterministic testing.
            clock_fn: Injected monotonic clock, for deterministic downtime measurement.

        Callback exceptions are logged and swallowed: a supervision loop must outlive a
        faulty notification hook. Probe results are unaffected.

        Raises:
            IBGatewayError: on invalid arguments or an unresolvable host.
        """
        if poll_interval < 0:
            raise IBGatewayError(f"poll_interval must be >= 0, got {poll_interval}.")
        if unhealthy_threshold < 1:
            raise IBGatewayError(f"unhealthy_threshold must be >= 1, got {unhealthy_threshold}.")
        if max_polls is not None and max_polls < 1:
            raise IBGatewayError(f"max_polls must be >= 1 when set, got {max_polls}.")

        polls = 0
        successes = 0
        failures = 0
        disconnects = 0
        reconnects = 0
        downtime_total = 0.0
        consecutive_failures = 0
        healthy = True
        down_since: Optional[float] = None

        while max_polls is None or polls < max_polls:
            polls += 1
            is_up = self.probe_gateway_port(
                self.config.host, self.config.port, self.config.timeout_seconds
            )

            if is_up:
                successes += 1
                consecutive_failures = 0
                if not healthy:
                    downtime = clock_fn() - down_since if down_since is not None else 0.0
                    downtime_total += downtime
                    reconnects += 1
                    healthy = True
                    down_since = None
                    logger.info(
                        "IB Gateway socket restored on %s:%s after %.1fs down. Re-establish "
                        "the API client connection; a restarted Gateway does not retain "
                        "prior subscriptions or client sessions.",
                        self.config.host,
                        self.config.port,
                        downtime,
                    )
                    self._fire_callback(on_reconnect, downtime, "on_reconnect")
            else:
                failures += 1
                consecutive_failures += 1
                if healthy and consecutive_failures >= unhealthy_threshold:
                    healthy = False
                    down_since = clock_fn()
                    disconnects += 1
                    logger.warning(
                        "IB Gateway socket DOWN on %s:%s after %s consecutive failed probes.",
                        self.config.host,
                        self.config.port,
                        consecutive_failures,
                    )
                    self._fire_callback(on_disconnect, consecutive_failures, "on_disconnect")

            if poll_interval and (max_polls is None or polls < max_polls):
                sleep_fn(poll_interval)

        if not healthy and down_since is not None:
            downtime_total += clock_fn() - down_since

        return GatewayHealthReport(
            polls=polls,
            successful_probes=successes,
            failed_probes=failures,
            disconnect_events=disconnects,
            reconnect_events=reconnects,
            total_downtime_seconds=downtime_total,
            healthy_at_exit=healthy,
        )

    @staticmethod
    def _fire_callback(callback: Optional[Callable[[Any], None]], value: Any, label: str) -> None:
        if callback is None:
            return
        try:
            callback(value)
        except Exception:  # noqa: BLE001 - a faulty hook must not kill supervision
            logger.exception("%s callback raised; continuing health monitoring.", label)

    def generate_docker_spec(self) -> Dict[str, Any]:
        """Generates a Compose spec for a headless IB Gateway container.

        Applies four deployment invariants that the defaults do not give you:

        1. The published port binds to `config.bind_address` (loopback by default). The
           TWS API socket has no credential of its own, so publishing it on 0.0.0.0 gives
           order-entry access to anything that can route to the host.
        2. The host port maps to the image's socat relay port, not to the Gateway port --
           Gateway listens on the container's 127.0.0.1 only.
        3. READ_ONLY_API follows `config.read_only_api`, which defaults to the protective
           setting IBKR itself defaults to.
        4. The healthcheck uses bash /dev/tcp, which exists in this image; `nc` does not.

        Raises:
            IBGatewayError: if `config.port` is not an IB Gateway port (this generator
                covers the IB Gateway image, not the TWS remote-desktop image).
        """
        relay_port = SOCAT_RELAY_PORTS.get(self.config.port)
        if relay_port is None:
            raise IBGatewayError(
                f"generate_docker_spec supports IB Gateway ports "
                f"{sorted(SOCAT_RELAY_PORTS)}; got {self.config.port}. For TWS or a custom "
                "socket port, write the port mapping against that deployment's own relay."
            )

        if not self.config.is_paper:
            logger.warning("Generating LIVE-mode container spec: orders will use real capital.")
        if not self.config.read_only_api and not self.config.is_paper:
            logger.warning(
                "READ_ONLY_API is disabled for a LIVE account; the container will accept "
                "order-entry API calls."
            )
        if self.config.bind_address not in _LOOPBACK_BIND_ADDRESSES:
            logger.warning(
                "Publishing the IB Gateway API port on %s exposes unauthenticated order "
                "entry beyond this host. Prefer a loopback bind, an SSH tunnel, or a "
                "shared Docker network.",
                self.config.bind_address,
            )
        if self.config.auto_restart_time is None:
            logger.warning(
                "auto_restart_time is not set; the Gateway session will not restart on a "
                "schedule and will need a manual 2FA login after IBKR's weekly credential "
                "expiry (Sundays 01:00 ET)."
            )

        environment: Dict[str, str] = {
            "TWS_USERID": "${IBKR_USERID}",
            "TRADING_MODE": "paper" if self.config.is_paper else "live",
            "READ_ONLY_API": "yes" if self.config.read_only_api else "no",
            "TIME_ZONE": self.config.time_zone,
        }
        if self.config.auto_restart_time is not None:
            environment["AUTO_RESTART_TIME"] = self.config.auto_restart_time

        service: Dict[str, Any] = {
            "image": self.config.image,
            "container_name": "ibkr_gateway_headless",
            # `unless-stopped`, not `always`: an operator who stops this container during
            # an incident must not have it resurrected by a Docker daemon restart.
            "restart": "unless-stopped",
            "environment": environment,
            "ports": [f"{self.config.bind_address}:{self.config.port}:{relay_port}"],
            "healthcheck": {
                "test": ["CMD-SHELL", _HEALTHCHECK_TEMPLATE.format(port=relay_port)],
                "interval": "30s",
                "timeout": "5s",
                "retries": 3,
                "start_period": "60s",
            },
        }

        spec: Dict[str, Any] = {"services": {"ib-gateway": service}}

        if self.config.use_password_file:
            environment["TWS_PASSWORD_FILE"] = "/run/secrets/ibkr_password"
            service["secrets"] = ["ibkr_password"]
            spec["secrets"] = {"ibkr_password": {"file": "./secrets/ibkr_password.txt"}}
        else:
            environment["TWS_PASSWORD"] = "${IBKR_PASSWORD}"

        return spec
